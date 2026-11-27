// SPDX-License-Identifier: MIT
#import <AudioToolbox/AudioToolbox.h>
#import <CoreAudio/AudioHardwareTapping.h>
#import <CoreAudio/CATapDescription.h>
#import <Foundation/Foundation.h>
#include <math.h>
#include <signal.h>
#include <stdatomic.h>
#include <unistd.h>

static volatile sig_atomic_t interrupted;
static void stopCapture(int signalNumber) { interrupted = 1; }

static OSStatus property(AudioObjectID object, AudioObjectPropertySelector selector,
                         AudioObjectPropertyScope scope, UInt32 size, void *value) {
    AudioObjectPropertyAddress address = {selector, scope, kAudioObjectPropertyElementMain};
    return AudioObjectGetPropertyData(object, &address, 0, NULL, &size, value);
}

static NSArray<NSNumber *> *objects(AudioObjectID object, AudioObjectPropertySelector selector,
                                    AudioObjectPropertyScope scope) {
    AudioObjectPropertyAddress address = {selector, scope, kAudioObjectPropertyElementMain};
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(object, &address, 0, NULL, &size) || !size) return @[];
    NSMutableData *data = [NSMutableData dataWithLength:size];
    if (AudioObjectGetPropertyData(object, &address, 0, NULL, &size, data.mutableBytes)) return @[];
    const AudioObjectID *ids = data.bytes;
    NSMutableArray *result = [NSMutableArray array];
    for (NSUInteger i = 0; i < size / sizeof(*ids); i++) [result addObject:@(ids[i])];
    return result;
}

static NSString *label(AudioObjectID object, AudioObjectPropertySelector selector) {
    CFStringRef name = NULL;
    if (property(object, selector, kAudioObjectPropertyScopeGlobal, sizeof(name), &name)) return nil;
    return CFBridgingRelease(name);
}

static BOOL succeeded(OSStatus status, const char *operation) {
    if (!status) return YES;
    fprintf(stderr, "%s failed (Core Audio status %d).\n", operation, (int)status);
    return NO;
}

typedef struct {
    float *stereo;
    UInt32 targetFrames;
    atomic_uint frames;
    atomic_bool invalidFormat;
} Recording;

static OSStatus collect(AudioDeviceID device, const AudioTimeStamp *now,
                        const AudioBufferList *input, const AudioTimeStamp *inputTime,
                        AudioBufferList *output, const AudioTimeStamp *outputTime, void *context) {
    Recording *recording = context;
    if (!input || !input->mNumberBuffers) return noErr;
    const AudioBuffer *left = &input->mBuffers[0];
    if (!left->mData || !left->mNumberChannels) return noErr;
    BOOL planar = left->mNumberChannels == 1;
    const AudioBuffer *right = left;
    if (planar) {
        if (input->mNumberBuffers < 2 || !input->mBuffers[1].mData ||
            input->mBuffers[1].mNumberChannels != 1 ||
            input->mBuffers[1].mDataByteSize != left->mDataByteSize) {
            atomic_store(&recording->invalidFormat, true);
            return noErr;
        }
        right = &input->mBuffers[1];
    }
    UInt32 start = atomic_load(&recording->frames);
    UInt32 available = left->mDataByteSize / (sizeof(float) * left->mNumberChannels);
    UInt32 count = MIN(available, recording->targetFrames - start);
    const float *leftSamples = left->mData;
    const float *rightSamples = right->mData;
    for (UInt32 i = 0; i < count; i++) {
        recording->stereo[2 * (start + i)] = leftSamples[i * left->mNumberChannels];
        recording->stereo[2 * (start + i) + 1] = rightSamples[i * right->mNumberChannels + !planar];
    }
    atomic_store(&recording->frames, start + count);
    return noErr;
}

static BOOL saveWav(NSURL *path, const Recording *recording, double rate) {
    AudioStreamBasicDescription stereo = {rate, kAudioFormatLinearPCM,
        kAudioFormatFlagsNativeFloatPacked, 8, 1, 8, 2, 32, 0};
    NSURL *tmp = [path.URLByDeletingLastPathComponent URLByAppendingPathComponent:NSUUID.UUID.UUIDString];
    AudioFileID file = NULL;
    if (!succeeded(AudioFileCreateWithURL((__bridge CFURLRef)tmp, kAudioFileWAVEType,
                                         &stereo, 0, &file), "Create WAV")) return NO;
    UInt32 expectedBytes = recording->targetFrames * 2 * sizeof(float);
    UInt32 bytes = expectedBytes;
    BOOL written = succeeded(AudioFileWriteBytes(file, false, 0, &bytes, recording->stereo), "Write WAV");
    BOOL closed = succeeded(AudioFileClose(file), "Close WAV");
    NSError *error = nil;
    if (written && closed && bytes == expectedBytes &&
        [NSFileManager.defaultManager moveItemAtURL:tmp toURL:path error:&error]) return YES;
    if (error) fprintf(stderr, "Save WAV: %s\n", error.localizedDescription.UTF8String);
    if (bytes != expectedBytes) fprintf(stderr, "Incomplete WAV write.\n");
    [NSFileManager.defaultManager removeItemAtURL:tmp error:NULL];
    return NO;
}

static int capture(AudioObjectID device, NSArray<NSNumber *> *processes, int seconds, NSURL *path) {
    NSString *uid = label(device, kAudioDevicePropertyDeviceUID);
    if (!uid) {
        fprintf(stderr, "The selected output device is no longer available.\n");
        return 1;
    }
    CATapDescription *description = [[CATapDescription alloc] initWithProcesses:processes
                                                                   andDeviceUID:uid withStream:0];
    description.privateTap = YES;
    description.muteBehavior = CATapUnmuted;
    AudioObjectID tap = kAudioObjectUnknown;
    AudioDeviceID aggregate = kAudioObjectUnknown;
    AudioDeviceIOProcID callback = NULL;
    AudioStreamBasicDescription format = {0};
    Recording recording = {0};
    double deviceRate = 0, aggregateRate = 0;
    NSDictionary *configuration = @{
        @kAudioAggregateDeviceNameKey: @"Mix Listener capture",
        @kAudioAggregateDeviceUIDKey: NSUUID.UUID.UUIDString,
        @kAudioAggregateDeviceIsPrivateKey: @YES,
        @kAudioAggregateDeviceTapListKey: @[@{
            @kAudioSubTapUIDKey: description.UUID.UUIDString,
            @kAudioSubTapDriftCompensationKey: @NO
        }]
    };
    BOOL started = NO;
    int result = 1;

    if (!succeeded(AudioHardwareCreateProcessTap(description, &tap), "Create process tap") ||
        !succeeded(property(tap, kAudioTapPropertyFormat, kAudioObjectPropertyScopeGlobal,
                            sizeof(format), &format), "Read tap format") ||
        !succeeded(property(device, kAudioDevicePropertyNominalSampleRate, kAudioObjectPropertyScopeGlobal,
                            sizeof(deviceRate), &deviceRate), "Read device rate")) goto cleanup;
    if (!(deviceRate > 0 && deviceRate == floor(deviceRate)) ||
        deviceRate * seconds > UINT32_MAX / (2 * sizeof(float)) ||
        format.mSampleRate != deviceRate || format.mChannelsPerFrame < 2 ||
        format.mFormatID != kAudioFormatLinearPCM || format.mBitsPerChannel != 32 ||
        !(format.mFormatFlags & kAudioFormatFlagIsFloat) ||
        (format.mFormatFlags & kAudioFormatFlagIsBigEndian)) {
        fprintf(stderr, "The first output stream must provide native-rate float32 PCM with at least two channels.\n");
        goto cleanup;
    }
    if (!succeeded(AudioHardwareCreateAggregateDevice((__bridge CFDictionaryRef)configuration, &aggregate),
                   "Create private capture device") ||
        !succeeded(property(aggregate, kAudioDevicePropertyNominalSampleRate, kAudioObjectPropertyScopeGlobal,
                            sizeof(aggregateRate), &aggregateRate), "Read capture rate")) goto cleanup;
    if (aggregateRate != deviceRate) {
        fprintf(stderr, "Core Audio did not expose the tap at the output device's rate.\n");
        goto cleanup;
    }
    recording.targetFrames = (UInt32)(seconds * deviceRate);
    recording.stereo = calloc(recording.targetFrames, 2 * sizeof(float));
    if (!recording.stereo) {
        fprintf(stderr, "Could not allocate capture buffer.\n");
        goto cleanup;
    }
    if (!succeeded(AudioDeviceCreateIOProcID(aggregate, collect, &recording, &callback), "Create capture callback") ||
        !succeeded(AudioDeviceStart(aggregate, callback), "Start capture")) goto cleanup;
    started = YES;
    fprintf(stderr, "Recording channels 1–2 on %s at %.0f Hz.\n",
            label(device, kAudioObjectPropertyName).UTF8String, deviceRate);

    double deadline = NSProcessInfo.processInfo.systemUptime + seconds + 10;
    while (!interrupted && !atomic_load(&recording.invalidFormat) &&
           atomic_load(&recording.frames) < recording.targetFrames &&
           NSProcessInfo.processInfo.systemUptime < deadline) usleep(10000);

    if (!succeeded(AudioDeviceStop(aggregate, callback), "Stop capture")) goto cleanup;
    started = NO;
    if (interrupted) {
        result = 130;
    } else if (atomic_load(&recording.invalidFormat) ||
               atomic_load(&recording.frames) != recording.targetFrames) {
        fprintf(stderr, "No complete stereo passage. Check playback and System Audio Recording permission.\n");
    } else if (saveWav(path, &recording, deviceRate)) {
        result = 0;
    }

cleanup:
    if (started) AudioDeviceStop(aggregate, callback);
    if (callback) AudioDeviceDestroyIOProcID(aggregate, callback);
    if (aggregate) AudioHardwareDestroyAggregateDevice(aggregate);
    if (tap) AudioHardwareDestroyProcessTap(tap);
    free(recording.stereo);
    return result;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        NSArray *devices = objects(kAudioObjectSystemObject, kAudioHardwarePropertyDevices,
                                   kAudioObjectPropertyScopeGlobal);
        if (argc == 2 && strcmp(argv[1], "devices") == 0) {
            for (NSNumber *device in devices) {
                if (objects(device.unsignedIntValue, kAudioDevicePropertyStreams, kAudioObjectPropertyScopeOutput).count) {
                    NSString *name = label(device.unsignedIntValue, kAudioObjectPropertyName);
                    if (name) puts(name.UTF8String);
                }
            }
            return 0;
        }
        if (argc != 5) {
            fprintf(stderr, "Usage: mix-capture BUNDLE_ID DEVICE_NAME_OR_EMPTY SECONDS OUTPUT.wav\n");
            return 2;
        }
        char *end = NULL;
        long seconds = strtol(argv[3], &end, 10);
        if (!*argv[3] || *end || seconds < 2 || seconds > 30) return 2;

        NSString *bundleID = @(argv[1]);
        NSString *deviceName = @(argv[2]);
        NSMutableDictionary<NSNumber *, NSMutableArray<NSNumber *> *> *byDevice = [NSMutableDictionary dictionary];
        for (NSNumber *process in objects(kAudioObjectSystemObject, kAudioHardwarePropertyProcessObjectList,
                                          kAudioObjectPropertyScopeGlobal)) {
            AudioObjectID processID = process.unsignedIntValue;
            if (![label(processID, kAudioProcessPropertyBundleID).lowercaseString isEqualToString:bundleID.lowercaseString]) continue;
            UInt32 running = 0;
            if (property(processID, kAudioProcessPropertyIsRunningOutput, kAudioObjectPropertyScopeGlobal,
                         sizeof(running), &running) || !running) continue;
            for (NSNumber *device in objects(processID, kAudioProcessPropertyDevices, kAudioObjectPropertyScopeOutput)) {
                if (deviceName.length &&
                    ![label(device.unsignedIntValue, kAudioObjectPropertyName) isEqualToString:deviceName]) continue;
                if (!byDevice[device]) byDevice[device] = [NSMutableArray array];
                [byDevice[device] addObject:process];
            }
        }
        if (byDevice.count != 1) {
            if (!byDevice.count) {
                fprintf(stderr, "No active output for %s%s%s. Start playback and check the app's output device.\n",
                        argv[1], deviceName.length ? " on " : "", argv[2]);
            } else {
                fprintf(stderr, "Multiple active outputs for %s. Use --device with an exact name from devices.\n", argv[1]);
            }
            return 1;
        }

        signal(SIGINT, stopCapture);
        signal(SIGTERM, stopCapture);
        NSNumber *device = byDevice.allKeys.firstObject;
        return capture(device.unsignedIntValue, byDevice[device], (int)seconds,
                       [NSURL fileURLWithPath:@(argv[4])]);
    }
}

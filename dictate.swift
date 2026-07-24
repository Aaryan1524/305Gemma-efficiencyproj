// dictate — speak one line, get it back as text.
//
// Transcription is on-device via Speech.framework; if the local model is
// unavailable we exit non-zero rather than quietly shipping audio to Apple.
//
// Two ways to get the transcript out:
//   stdout            — when run straight from a shell
//   --out <path>      — written continuously (partials included), plus a
//                       marker file <path>.done on exit. This is the mode the
//                       notch uses, because it launches us through `open`,
//                       which gives the caller no stdout to read.
//
// Why a separate binary at all: TCC kills any process that touches the mic
// without an Info.plist usage description, and a bare `python` has none.
// The descriptions below are linked in as a __TEXT,__info_plist section.
//
// Build:
//   ./build_dictate.sh
// which does roughly:
//   swiftc dictate.swift -o dictate \
//     -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
//     -Xlinker dictate-Info.plist
//   codesign --force --sign - dictate
// and then wraps it in Dictate.app so `open` can launch it as its own
// TCC-responsible process.

import AVFoundation
import Foundation
import Speech

let MAX_SECONDS = 20.0      // hard cap
let SILENCE_STOP = 1.8      // stop this long after speech stops

// ---- output plumbing --------------------------------------------------
let argv = CommandLine.arguments
let outPath: String? = {
    if let i = argv.firstIndex(of: "--out"), i + 1 < argv.count { return argv[i + 1] }
    return nil
}()

func note(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

/// Mirror the current text to --out so the caller can show it live.
func publish(_ s: String) {
    guard let p = outPath else { return }
    try? s.write(toFile: p, atomically: true, encoding: .utf8)
}

/// Signal "no more updates coming" so the caller can stop polling.
func markDone() {
    guard let p = outPath else { return }
    FileManager.default.createFile(atPath: p + ".done", contents: Data())
}

func fail(_ s: String, _ code: Int32) -> Never {
    note("ERR: " + s)
    publish("ERR: " + s)
    markDone()
    exit(code)
}

// ---- 1. authorization -------------------------------------------------
let sem = DispatchSemaphore(value: 0)
var speechOK = false
SFSpeechRecognizer.requestAuthorization { st in
    speechOK = (st == .authorized)
    sem.signal()
}
sem.wait()
guard speechOK else { fail("speech recognition not authorized", 2) }

var micOK = false
AVCaptureDevice.requestAccess(for: .audio) { granted in
    micOK = granted
    sem.signal()
}
sem.wait()
guard micOK else { fail("microphone not authorized", 3) }

// ---- 2. recognizer, on-device only ------------------------------------
guard let rec = SFSpeechRecognizer(locale: Locale(identifier: "en-US")), rec.isAvailable else {
    fail("recognizer unavailable", 4)
}
note("supportsOnDevice=\(rec.supportsOnDeviceRecognition)")
guard rec.supportsOnDeviceRecognition else {
    fail("on-device model unavailable (refusing to use cloud)", 5)
}

let req = SFSpeechAudioBufferRecognitionRequest()
req.shouldReportPartialResults = true
req.requiresOnDeviceRecognition = true

// ---- 3. audio ---------------------------------------------------------
let engine = AVAudioEngine()
let node = engine.inputNode
let fmt = node.outputFormat(forBus: 0)
note("input format: \(fmt.sampleRate)Hz channels=\(fmt.channelCount)")
guard fmt.sampleRate > 0, fmt.channelCount > 0 else {
    fail("input device has no usable format — check System Settings > Sound > Input", 8)
}

let meter = NSLock()
var bufCount = 0
var peak: Float = 0

node.installTap(onBus: 0, bufferSize: 1024, format: fmt) { buf, _ in
    req.append(buf)
    if let ch = buf.floatChannelData?[0] {
        var p: Float = 0
        for i in 0..<Int(buf.frameLength) { p = max(p, abs(ch[i])) }
        meter.lock(); bufCount += 1; peak = max(peak, p); meter.unlock()
    }
}
engine.prepare()
do { try engine.start() } catch { fail("audio engine: \(error.localizedDescription)", 6) }
note(">>> SPEAK NOW <<<")

// ---- 4. recognize -----------------------------------------------------
let lock = NSLock()
var text = ""
var lastChange = Date()
var finished = false

let task = rec.recognitionTask(with: req) { result, error in
    lock.lock()
    if let r = result {
        let s = r.bestTranscription.formattedString
        if s != text {
            text = s
            lastChange = Date()
            note("… \(s)")
            publish(s)
        }
        if r.isFinal { finished = true }
    }
    if let e = error {
        note("ERR cb: \(e)")
        finished = true
    }
    lock.unlock()
}

let start = Date()
while true {
    // Run the run loop rather than sleeping: recognition callbacks are
    // delivered through it, and sleeping here starves them.
    RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    lock.lock()
    let done = finished
    let hasText = !text.isEmpty
    let quiet = Date().timeIntervalSince(lastChange)
    lock.unlock()
    if done { break }
    if hasText && quiet > SILENCE_STOP { break }
    if Date().timeIntervalSince(start) > MAX_SECONDS { break }
}

engine.stop()
node.removeTap(onBus: 0)
req.endAudio()
task.cancel()

meter.lock(); let finalBufs = bufCount; let finalPeak = peak; meter.unlock()
note("captured \(finalBufs) buffers, peak amplitude \(finalPeak)")
if finalBufs == 0 {
    fail("mic delivered zero buffers — check Microphone permission", 9)
}

lock.lock()
let final = text.trimmingCharacters(in: .whitespacesAndNewlines)
lock.unlock()

guard !final.isEmpty else { fail("heard nothing", 7) }
publish(final)
markDone()
print(final)          // stdout = the transcript, and nothing else

// dictate — speak one line, get it on stdout.
//
// Standalone CLI (no .app bundle): the usage descriptions TCC demands are
// linked straight into the binary as a __TEXT,__info_plist section.
// Transcription is forced on-device; if the local model isn't available we
// exit non-zero rather than quietly shipping audio to Apple.
//
// Build (the -sectcreate flags are what keep TCC from killing the process):
//   swiftc dictate.swift -o dictate \
//     -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
//     -Xlinker dictate-Info.plist
//   codesign --force --sign - dictate

import AVFoundation
import Foundation
import Speech

let MAX_SECONDS = 20.0      // hard cap
let SILENCE_STOP = 1.8      // stop this long after speech stops

func note(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

func fail(_ s: String, _ code: Int32) -> Never {
    note("ERR: " + s)
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

// Track what the mic is actually delivering, so "heard nothing" can be told
// apart from "mic delivered pure silence".
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
note(">>> SPEAK NOW — say your goals for today <<<")

// ---- 4. recognize -----------------------------------------------------
let lock = NSLock()
var text = ""
var lastChange = Date()
var finished = false

var callbacks = 0

let task = rec.recognitionTask(with: req) { result, error in
    lock.lock()
    callbacks += 1
    if let r = result {
        let s = r.bestTranscription.formattedString
        if s != text { text = s; lastChange = Date(); note("… \(s)") }
        if r.isFinal { finished = true }
    }
    if let e = error {
        note("ERR cb: \(e)")          // never swallow this — it names the real cause
        finished = true
    }
    lock.unlock()
}

let start = Date()
var lastTick = 0
while true {
    // Must run the run loop, not sleep: recognition callbacks are delivered
    // through it. Sleeping here starves them and no result ever arrives.
    RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    lock.lock()
    let done = finished
    let hasText = !text.isEmpty
    let quiet = Date().timeIntervalSince(lastChange)
    lock.unlock()

    // Once a second, report how much audio arrived and how loud it was.
    let elapsed = Int(Date().timeIntervalSince(start))
    if elapsed > lastTick {
        lastTick = elapsed
        meter.lock(); let n = bufCount; let p = peak; meter.unlock()
        lock.lock(); let cb = callbacks; lock.unlock()
        note(String(format: "  %ds buffers=%d peak=%.4f callbacks=%d%@",
                    elapsed, n, p, cb, p < 0.001 ? "  (SILENT — mic delivering nothing)" : ""))
    }

    if done { break }
    if hasText && quiet > SILENCE_STOP { break }
    if Date().timeIntervalSince(start) > MAX_SECONDS { break }
}

meter.lock(); let finalBufs = bufCount; let finalPeak = peak; meter.unlock()
note("captured \(finalBufs) buffers, peak amplitude \(finalPeak)")
if finalBufs == 0 {
    fail("mic delivered zero buffers — Terminal likely lacks Microphone permission "
         + "(System Settings > Privacy & Security > Microphone)", 9)
}
if finalPeak < 0.001 {
    fail("mic delivered only silence — check the input device and volume", 10)
}

engine.stop()
node.removeTap(onBus: 0)
req.endAudio()
task.cancel()

lock.lock()
let final = text.trimmingCharacters(in: .whitespacesAndNewlines)
lock.unlock()

guard !final.isEmpty else { fail("heard nothing", 7) }
print(final)          // stdout = the transcript, and nothing else

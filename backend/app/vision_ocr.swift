import Foundation
import Vision
import ImageIO

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = true
let handler = VNImageRequestHandler(url: URL(fileURLWithPath: CommandLine.arguments[1]))
do {
    try handler.perform([request])
    let lines = (request.results as? [VNRecognizedTextObservation] ?? []).compactMap { $0.topCandidates(1).first?.string }
    let data = try JSONSerialization.data(withJSONObject: lines)
    print(String(data: data, encoding: .utf8)!)
} catch {
    fputs("Local OCR failed\n", stderr)
    exit(1)
}

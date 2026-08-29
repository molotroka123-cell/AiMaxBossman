import Foundation

public struct SSEMessage: Equatable, Sendable {
    public let event: String
    public let data: String
}

public struct SSEParser: Sendable {
    private var buffer = ""
    public init() {}
    public mutating func push(_ chunk: String) -> [SSEMessage] {
        buffer += chunk.replacingOccurrences(of: "\r\n", with: "\n")
        var out: [SSEMessage] = []
        while let range = buffer.range(of: "\n\n") {
            let block = String(buffer[..<range.lowerBound])
            buffer = String(buffer[range.upperBound...])
            if let msg = Self.parse(block) { out.append(msg) }
        }
        return out
    }
    private static func parse(_ block: String) -> SSEMessage? {
        guard !block.isEmpty, !block.hasPrefix(":") else { return nil }
        var event = "message"; var data: [String] = []
        for line in block.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix("event:") { event = line.dropFirst(6).trimmingCharacters(in: .whitespaces) }
            if line.hasPrefix("data:") { data.append(line.dropFirst(5).trimmingCharacters(in: .whitespaces)) }
        }
        return data.isEmpty ? nil : SSEMessage(event: event, data: data.joined(separator: "\n"))
    }
}

import Foundation

public struct WhoAmI: Codable, Sendable {
    public let deviceId: String
    public let sessionId: String?
    public let name: String
    public let scopes: [String]
    enum CodingKeys: String, CodingKey { case deviceId = "device_id", sessionId = "session_id", name, scopes }
}

public struct SessionOpen: Codable, Sendable {
    public let deviceId: String
    public let sessionId: String
    public let sessionToken: String
    public let scopes: [String]
    enum CodingKeys: String, CodingKey { case deviceId = "device_id", sessionId = "session_id", sessionToken = "session_token", scopes }
}

public struct RemoteTask: Codable, Identifiable, Sendable {
    public let id: Int
    public let agent: String?
    public let source: String?
    public let text: String
    public let status: String
    public let result: String?
    public let error: String?
    public let createdAt: String?
    enum CodingKeys: String, CodingKey { case id, agent, source, text, status, result, error, createdAt = "created_at" }
}

public struct Approval: Codable, Identifiable, Sendable {
    public let id: Int
    public let agent: String?
    public let kind: String?
    public let preview: String?
    public let status: String
}

public struct AgentCard: Codable, Identifiable, Sendable {
    public var id: String { name }
    public let name: String
    public let title: String
    public let model: String
}

public struct NewTask: Codable, Sendable {
    public let text: String
    public let agent: String?
    public init(text: String, agent: String?) { self.text = text; self.agent = agent }
}

public struct Decision: Codable, Sendable { public let approve: Bool; public init(approve: Bool) { self.approve = approve } }

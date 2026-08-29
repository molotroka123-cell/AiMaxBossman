import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum BossmanAPIError: Error, LocalizedError, Sendable {
    case invalidBaseURL, insecureTransport, http(Int, String), invalidResponse
    public var errorDescription: String? {
        switch self {
        case .invalidBaseURL: return "Invalid BOSSMAN URL"
        case .insecureTransport: return "BOSSMAN remote requires HTTPS"
        case let .http(code, message): return "HTTP \(code): \(message)"
        case .invalidResponse: return "Invalid server response"
        }
    }
}

public final class BossmanAPI: @unchecked Sendable {
    public let baseURL: URL
    private let session: URLSession
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    public init(baseURL: URL, session: URLSession = .shared, allowHTTPForLocalhost: Bool = false) throws {
        guard let scheme = baseURL.scheme?.lowercased(), baseURL.host != nil else { throw BossmanAPIError.invalidBaseURL }
        if scheme != "https" {
            let local = ["localhost", "127.0.0.1", "::1"].contains(baseURL.host ?? "")
            guard allowHTTPForLocalhost && local else { throw BossmanAPIError.insecureTransport }
        }
        self.baseURL = baseURL
        self.session = session
    }

    private func request(_ path: String, method: String = "GET", token: String? = nil, body: Data? = nil) -> URLRequest {
        let relative = path.hasPrefix("/") ? String(path.dropFirst()) : path
        var req = URLRequest(url: baseURL.appending(path: relative))
        req.httpMethod = method
        req.timeoutInterval = 30
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if body != nil { req.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        req.httpBody = body
        return req
    }

    private func send<T: Decodable>(_ req: URLRequest, as type: T.Type) async throws -> T {
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw BossmanAPIError.invalidResponse }
        guard 200..<300 ~= http.statusCode else {
            let message = String(data: data.prefix(2000), encoding: .utf8) ?? "request failed"
            throw BossmanAPIError.http(http.statusCode, message)
        }
        return try decoder.decode(T.self, from: data)
    }

    public func openSession(deviceToken: String) async throws -> SessionOpen {
        try await send(request("/remote/auth", method: "POST", token: deviceToken), as: SessionOpen.self)
    }
    public func whoami(token: String) async throws -> WhoAmI { try await send(request("/remote/whoami", token: token), as: WhoAmI.self) }
    public func tasks(token: String) async throws -> [RemoteTask] { try await send(request("/remote/tasks?limit=50", token: token), as: [RemoteTask].self) }
    public func agents(token: String) async throws -> [AgentCard] { try await send(request("/remote/agents", token: token), as: [AgentCard].self) }
    public func approvals(token: String) async throws -> [Approval] { try await send(request("/remote/approvals?status=pending&limit=50", token: token), as: [Approval].self) }
    public func createTask(_ task: NewTask, token: String) async throws -> RemoteTask {
        try await send(request("/remote/tasks", method: "POST", token: token, body: try encoder.encode(task)), as: RemoteTask.self)
    }
    public func decide(_ id: Int, approve: Bool, token: String) async throws {
        struct AnyReply: Decodable {}
        _ = try await send(request("/remote/approvals/\(id)", method: "POST", token: token, body: try encoder.encode(Decision(approve: approve))), as: AnyReply.self)
    }
    public func logout(token: String) async throws {
        struct Reply: Decodable { let ok: Bool }
        _ = try await send(request("/remote/session/logout", method: "POST", token: token), as: Reply.self)
    }
}

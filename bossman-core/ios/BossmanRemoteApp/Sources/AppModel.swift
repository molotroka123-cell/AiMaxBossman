import Foundation
import BossmanRemoteKit

@MainActor
final class AppModel: ObservableObject {
    @Published var baseURL = UserDefaults.standard.string(forKey: "baseURL") ?? ""
    @Published var sessionToken = KeychainStore.get("session") ?? ""
    @Published var who: WhoAmI?
    @Published var tasks: [RemoteTask] = []
    @Published var approvals: [Approval] = []
    @Published var agents: [AgentCard] = []
    @Published var error = ""
    var connected: Bool { who != nil && !sessionToken.isEmpty }

    private func client() throws -> BossmanAPI {
        guard let url = URL(string: baseURL) else { throw BossmanAPIError.invalidBaseURL }
        return try BossmanAPI(baseURL: url)
    }

    func connect(deviceToken: String) async {
        do {
            let api = try client(); let session = try await api.openSession(deviceToken: deviceToken)
            try KeychainStore.set(session.sessionToken, account: "session")
            sessionToken = session.sessionToken; UserDefaults.standard.set(baseURL, forKey: "baseURL")
            try await refresh()
        } catch { self.error = error.localizedDescription }
    }

    func refresh() async throws {
        let api = try client(); let token = sessionToken
        who = try await api.whoami(token: token)
        async let t = api.tasks(token: token); async let a = api.agents(token: token)
        tasks = try await t; agents = try await a
        if who?.scopes.contains("approve") == true { approvals = try await api.approvals(token: token) } else { approvals = [] }
    }

    func create(text: String, agent: String?) async {
        do { _ = try await client().createTask(NewTask(text: text, agent: agent), token: sessionToken); try await refresh() }
        catch { self.error = error.localizedDescription }
    }

    func decide(id: Int, approve: Bool) async {
        do { try await client().decide(id, approve: approve, token: sessionToken); try await refresh() }
        catch { self.error = error.localizedDescription }
    }

    func logout() async {
        if let api = try? client(), !sessionToken.isEmpty { try? await api.logout(token: sessionToken) }
        KeychainStore.delete("session"); sessionToken = ""; who = nil; tasks = []; approvals = []
    }
}

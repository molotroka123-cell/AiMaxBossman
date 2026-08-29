import SwiftUI

struct RootView: View {
    @EnvironmentObject var model: AppModel
    @State private var deviceToken = ""
    @State private var taskText = ""
    @State private var selectedAgent = ""

    var body: some View {
        NavigationStack {
            Group {
                if model.connected { dashboard } else { login }
            }
            .navigationTitle("BOSSMAN")
            .alert("Ошибка", isPresented: Binding(get: { !model.error.isEmpty }, set: { if !$0 { model.error = "" } })) { Button("OK") { model.error = "" } } message: { Text(model.error) }
        }
        .task { if !model.sessionToken.isEmpty { try? await model.refresh() } }
    }

    private var login: some View {
        Form {
            Section("Private endpoint") { TextField("https://bossman.tailnet.ts.net", text: $model.baseURL).textInputAutocapitalization(.never).keyboardType(.URL) }
            Section("Device token") { SecureField("rcd_…", text: $deviceToken).textInputAutocapitalization(.never) }
            Button("Открыть сессию") { let token = deviceToken; deviceToken = ""; Task { await model.connect(deviceToken: token) } }.disabled(model.baseURL.isEmpty || deviceToken.isEmpty)
        }
    }

    private var dashboard: some View {
        List {
            Section("Новая задача") {
                Picker("Agent", selection: $selectedAgent) { Text("Auto").tag(""); ForEach(model.agents) { Text("\($0.title) · \($0.model)").tag($0.name) } }
                TextEditor(text: $taskText).frame(minHeight: 90)
                Button("Отправить") { let text = taskText; taskText = ""; Task { await model.create(text: text, agent: selectedAgent.isEmpty ? nil : selectedAgent) } }.disabled(taskText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if !model.approvals.isEmpty {
                Section("Подтверждения") { ForEach(model.approvals) { a in VStack(alignment: .leading, spacing: 8) { Text(a.preview ?? a.kind ?? "Sensitive action").font(.caption).textSelection(.enabled); HStack { Button("Разрешить") { Task { await model.decide(id: a.id, approve: true) } }.tint(.green); Button("Отклонить", role: .destructive) { Task { await model.decide(id: a.id, approve: false) } } } } } }
            }
            Section("Задачи") { ForEach(model.tasks) { t in VStack(alignment: .leading, spacing: 6) { HStack { Text("#\(t.id) \(t.agent ?? "auto")").bold(); Spacer(); Text(t.status).font(.caption) }; Text(t.text); if let r=t.result { Text(r).font(.caption).foregroundStyle(.secondary) }; if let e=t.error { Text(e).font(.caption).foregroundStyle(.red) } } } }
            Section { Button("Обновить") { Task { try? await model.refresh() } }; Button("Выйти", role: .destructive) { Task { await model.logout() } } }
        }
        .refreshable { try? await model.refresh() }
    }
}

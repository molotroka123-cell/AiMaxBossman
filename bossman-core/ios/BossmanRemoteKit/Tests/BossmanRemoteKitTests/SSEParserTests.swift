import Testing
@testable import BossmanRemoteKit

@Test func parsesSplitSSE() {
    var p = SSEParser()
    #expect(p.push("event: task\ndata: {\"id\":1}").isEmpty)
    let out = p.push("\n\n")
    #expect(out == [SSEMessage(event: "task", data: "{\"id\":1}")])
}

@Test func ignoresKeepalive() {
    var p = SSEParser()
    #expect(p.push(": ping\n\n").isEmpty)
}

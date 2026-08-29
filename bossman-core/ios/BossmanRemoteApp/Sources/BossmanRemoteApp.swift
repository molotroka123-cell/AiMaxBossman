import SwiftUI
import BossmanRemoteKit

@main
struct BossmanRemoteApp: App {
    @StateObject private var model = AppModel()
    var body: some Scene { WindowGroup { RootView().environmentObject(model) } }
}

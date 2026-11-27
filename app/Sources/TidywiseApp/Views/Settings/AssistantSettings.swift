import SwiftUI

/// Lets AI assistants search the archive through the MCP server (`tidywise mcp`): one switch, one button per
/// assistant Tidywise knows, and a copy-and-paste configuration for every other one.
struct AssistantSettings: View {
    @Environment(AppState.self) private var state
    @Binding var config: TidywiseConfig

    var body: some View {
        SettingsCard {
            SettingToggle(symbol: "magnifyingglass", title: "Let assistants search your archive",
                          on: "On: assistants you connect can look up your sorted files and saved links. They can only read, and only while Tidywise is open.",
                          off: "Off: no assistant gets anything from Tidywise, even if it is connected.",
                          isOn: $config.mcpEnabled)
        }

        clients
        manual
        abilities
    }

    // MARK: One click

    private var clients: some View {
        VStack(alignment: .leading, spacing: 12) {
            CardTitle(title: "Connect with one click", systemImage: "bolt.fill")
            if let status = state.assistants {
                VStack(spacing: 2) {
                    ForEach(status.clients) { row($0) }
                }
            } else {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Looking for assistants on this Mac …").font(.callout).foregroundStyle(.secondary)
                }
                .padding(.horizontal, 10)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
        .task { await state.refreshAssistants() }
    }

    private func row(_ client: AssistantsStatus.Client) -> some View {
        let busy = state.assistantBusy == client.id
        return HStack(spacing: 12) {
            IconBadge(symbol: symbol(client.id), color: client.connected ? DS.ok : (client.installed ? DS.accent : .gray), size: 30)
            VStack(alignment: .leading, spacing: 1) {
                Text(client.title).font(.body.weight(.medium))
                Text(client.connected ? "Connected – new sessions can search your archive"
                     : (client.installed ? "Installed, not connected yet" : "Not found on this Mac"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            if busy {
                ProgressView().controlSize(.small)
            } else if client.connected {
                Label("Connected", systemImage: "checkmark").font(.callout.weight(.medium)).foregroundStyle(DS.ok)
                Button("Remove") { state.disconnectAssistant(client) }.buttonStyle(.borderless)
            } else if client.installed {
                Button("Connect") { state.connectAssistant(client) }.buttonStyle(.borderedProminent)
            }
        }
        .disabled(state.assistantBusy != nil)
        .hoverHighlight()
    }

    private func symbol(_ id: String) -> String {
        switch id {
        case "claude", "claude-desktop": "sparkle"
        case "codex": "chevron.left.forwardslash.chevron.right"
        case "cursor", "windsurf", "zed": "curlybraces"
        default: "terminal.fill"
        }
    }

    // MARK: Everything else

    private var manual: some View {
        VStack(alignment: .leading, spacing: 12) {
            CardTitle(title: "Any other assistant", systemImage: "puzzlepiece.extension")
            Text("Tidywise speaks MCP, the standard most AI assistants use for tools. Add this to the assistant's MCP settings – it works wherever a local (“stdio”) server can be configured.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let status = state.assistants {
                code(status.snippet, label: "Copy configuration")
                Text("If it only asks for a command:").font(.caption).foregroundStyle(.secondary)
                code(status.commandLine, label: "Copy command")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func code(_ text: String, label: String) -> some View {
        HStack(alignment: .top) {
            Text(text)
                .font(.system(.callout, design: .monospaced))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 12)
            Button { state.copyToClipboard(text) } label: { Label(label, systemImage: "doc.on.doc") }.controlSize(.small)
        }
        .well()
    }

    // MARK: What it can do

    private var abilities: some View {
        VStack(alignment: .leading, spacing: 10) {
            CardTitle(title: "What an assistant can do", systemImage: "list.bullet")
            ability("magnifyingglass", "Search", "Find sorted files, pages read and web searches by words, by the last days, or both.")
            ability("square.grid.2x2", "Topics", "Get your topics with counts, and where the archive lives.")
            Label("It cannot move, change or delete anything, and it never sees your private folder.", systemImage: "lock.fill")
                .font(.callout)
                .foregroundStyle(.secondary)
                .padding(.top, 2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func ability(_ symbol: String, _ title: String, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: symbol).foregroundStyle(.secondary).frame(width: 20)
            Text(title).font(.body.weight(.medium)).frame(width: 64, alignment: .leading)
            Text(text).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

import SwiftUI

/// All settings, in six sections. Every change applies immediately – there is no “Save” button.
/// The sections use the same building blocks as setup.
struct SettingsView: View {
    @Environment(AppState.self) private var state

    /// The real configuration: writing saves immediately through the CLI.
    private var config: Binding<TidywiseConfig> {
        Binding(get: { state.config }, set: { state.saveConfig($0) })
    }

    var body: some View {
        PageScroll("Settings", subtitle: subtitle) {
            tabs
            switch state.settingsTab {
            case .folders:
                WatchFoldersEditor(config: config)
                DestinationEditor(config: config)
            case .rules:
                RuleSettings(config: config)
            case .topics:
                TopicSettings(config: config)
            case .model:
                ModelPicker(config: config)
                SummaryLanguageSetting(config: config)
                ProfileField(config: config)
            case .assistants:
                AssistantSettings(config: config)
            case .general:
                GeneralSettings(config: config)
            }
        }
    }

    private var subtitle: String {
        switch state.settingsTab {
        case .folders: "Where Tidywise looks for loose files – and where it sorts them to."
        case .rules: "How Tidywise sorts: when, how independently and how carefully."
        case .topics: "Which topics Tidywise sorts into. Every topic is a folder."
        case .model: "Which AI on your Mac reads the files."
        case .assistants: "Let Claude, Codex, Cursor, Zed and others search what Tidywise has sorted."
        case .general: "Startup, automation and browser history."
        }
    }

    private var tabs: some View {
        HStack(spacing: 6) {
            ForEach(SettingsTab.allCases) { tab in
                let on = state.settingsTab == tab
                Button { state.settingsTab = tab } label: {
                    Label(tab.rawValue, systemImage: tab.icon)
                        .font(.callout.weight(.medium))
                        .foregroundStyle(on ? Color.white : Color.primary)
                        .padding(.horizontal, 13)
                        .padding(.vertical, 7)
                        .background(on ? AnyShapeStyle(DS.accent.gradient) : AnyShapeStyle(DS.surface), in: Capsule())
                        .overlay(Capsule().strokeBorder(Color.primary.opacity(on ? 0 : 0.10), lineWidth: 0.5))
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
            }
            Spacer(minLength: 0)
        }
    }
}

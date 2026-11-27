import AppKit
import SwiftUI

/// Explains the one macOS permission Tidywise needs and gets you there in two clicks.
struct AccessCard: View {
    @Environment(AppState.self) private var state
    /// In setup as a calm explanation, otherwise as a notice that something is missing.
    var urgent = true

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(urgent ? "macOS isn't letting Tidywise access your files yet" : "Allow access once",
                  systemImage: urgent ? "lock.fill" : "lock.open.fill")
                .font(.body.weight(.semibold))
            Text("macOS protects Downloads, Documents, Desktop and your browser history. For Tidywise to sort in the background, its helper program needs “Full Disk Access” once:")
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 6) {
                step(1, "Open System Settings (button below).")
                step(2, "Show the helper program in Finder and drag it into the list.")
                step(3, "Turn on the switch next to it.")
            }
            HStack(spacing: 8) {
                Button("Open System Settings") { state.openFullDiskAccessSettings() }.buttonStyle(.borderedProminent)
                Button("Show helper program in Finder") {
                    if let path = state.helperPath, !path.isEmpty {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
                    }
                }
                .disabled((state.helperPath ?? "").isEmpty)
                Button {
                    if let path = state.helperPath, !path.isEmpty { state.copyToClipboard(path) }
                } label: { Label("Copy path", systemImage: "doc.on.doc") }
                    .buttonStyle(.borderless)
                    .disabled((state.helperPath ?? "").isEmpty)
                    .help(state.helperPath ?? "")
            }
            Text("The helper program is Python – it runs the part of Tidywise that reads and moves the files. None of it leaves your Mac.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(tint: urgent ? DS.attention : nil)
    }

    private func step(_ n: Int, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(n)")
                .font(.caption.weight(.bold))
                .foregroundStyle(.white)
                .frame(width: 18, height: 18)
                .background(Color.secondary, in: Circle())
            Text(text).font(.callout)
        }
    }
}

/// App behavior: startup, automation, browser history, setup.
struct GeneralSettings: View {
    @Environment(AppState.self) private var state
    @Binding var config: TidywiseConfig
    @ViewState private var chromeProfile = ""

    var body: some View {
        if state.accessBlocked { AccessCard() }

        SettingsCard {
            SettingToggle(symbol: "power", title: "Start at login",
                          on: "On: Tidywise starts with your Mac in the menu bar and sorts in the background from then on.",
                          off: "Off: Tidywise only sorts once you open the app.",
                          isOn: Binding(get: { state.launchAtLogin }, set: { state.setLaunchAtLogin($0) }))
            Divider()
            SettingRow("clock.arrow.circlepath", "Automation",
                       state.service.loaded
                           ? "Set up: Tidywise checks \(state.intervalText) while the app is open."
                           : "Not set up: Tidywise only sorts when you click “Check for new files”.") {
                if state.service.loaded {
                    Button("Remove") { state.removeService() }.disabled(state.runningCommand != nil)
                } else {
                    Button("Set up") { state.installService() }.buttonStyle(.borderedProminent).disabled(state.runningCommand != nil)
                }
            }
        }

        SettingsCard {
            SettingToggle(symbol: "link", title: "Browser history as a link collection",
                          on: "On: Pages you visit are collected by topic and can be found under “Links”. Everything stays on your Mac.",
                          off: "Off: Tidywise doesn't read your browser history.",
                          isOn: $config.linksEnabled)
            if config.linksEnabled {
                Divider()
                browserChoice
            }
            if config.linksEnabled, config.chosenBrowsers.contains("chrome") {
                Divider()
                SettingRow("person.crop.circle", "Chrome profile",
                           "Only matters if you use several profiles in Chrome. The first is called “Default”, the others “Profile 1”, “Profile 2” …") {
                    TextField("Default", text: $chromeProfile)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 160)
                        .onSubmit(commitProfile)
                }
            }
        }
        .onAppear { chromeProfile = config.chromeProfile }
        .onDisappear(perform: commitProfile)

        SettingsCard {
            SettingRow("sparkles.rectangle.stack", "Setup",
                       "Go through the steps from the first launch again. Your settings stay as they are until you save at the end.") {
                Button("Show again") { state.beginSetup() }
            }
            Divider()
            SettingRow("doc.text", "Configuration file",
                       "All settings are in \(Paths.short(Paths.config.path)) – readable and editable by hand.") {
                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([Paths.config]) }
            }
        }
    }

    /// One switch per browser; at least one stays on while the link collection is on.
    private var browserChoice: some View {
        VStack(alignment: .leading, spacing: 10) {
            SettingLabel(symbol: "safari", title: "Browsers",
                         text: "Whose history Tidywise reads. Safari needs Full Disk Access – Tidywise tells you if it's missing.")
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 8)], alignment: .leading, spacing: 8) {
                ForEach(Browser.all) { browser in
                    Toggle(isOn: binding(for: browser.id)) {
                        HStack(spacing: 4) {
                            Text(browser.title)
                            if !browser.isInstalled { Text("not found").font(.caption).foregroundStyle(.tertiary) }
                        }
                    }
                    .toggleStyle(.checkbox)
                    .disabled(config.chosenBrowsers == [browser.id])
                }
            }
            .padding(.leading, 36)
        }
        .padding(.vertical, 12)
    }

    private func binding(for id: String) -> Binding<Bool> {
        Binding(get: { config.chosenBrowsers.contains(id) }, set: { on in
            var chosen = config.chosenBrowsers.filter { $0 != id }
            if on { chosen.append(id) }
            guard !chosen.isEmpty else { return }
            config.browsers = Browser.all.map(\.id).filter(chosen.contains)
        })
    }

    private func commitProfile() {
        let value = chromeProfile.trimmingCharacters(in: .whitespaces)
        if !value.isEmpty, value != config.chromeProfile { config.chromeProfile = value }
    }
}

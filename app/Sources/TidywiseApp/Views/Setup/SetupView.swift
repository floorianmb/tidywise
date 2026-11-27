import AppKit
import SwiftUI

/// Setup on first launch: five short steps, each with one question.
/// It works on a draft and only saves at the end – if you cancel, nothing has changed.
struct SetupView: View {
    @Environment(AppState.self) private var state
    @ViewState private var draft = TidywiseConfig()
    @ViewState private var topicPool: [Topic] = []
    @ViewState private var prepared = false
    @ViewState private var automation = true
    @ViewState private var launchAtLogin = false

    var body: some View {
        VStack(spacing: 0) {
            StepIndicator(current: state.setupStep)
                .padding(.top, 22)
                .padding(.bottom, 6)
            ScrollView {
                VStack(alignment: .leading, spacing: DS.gap) {
                    PageHeader(title, subtitle: subtitle).padding(.bottom, 4)
                    if !state.cliInstalled {
                        MissingInstallCard()
                    } else {
                        content
                    }
                }
                .frame(maxWidth: 800, alignment: .leading)
                .padding(DS.pagePadding)
                .frame(maxWidth: .infinity)
            }
            Divider()
            footer
        }
        .background(DS.page)
        .onAppear(perform: prepare)
        .onChange(of: state.configLoaded) { prepare() }
        .onChange(of: state.cliInstalled) { prepare() }
    }

    /// Fill the draft from the current state – the very first time with the model that fits this Mac.
    private func prepare() {
        guard !prepared, state.configLoaded, state.cliInstalled else { return }
        prepared = true
        draft = state.config
        topicPool = state.config.topics
        automation = state.config.exists ? state.service.loaded : true
        launchAtLogin = state.launchAtLogin
        if !state.config.exists, !state.isInstalled(draft.model) {
            draft.model = ModelCatalog.recommended(ramBytes: state.memoryTotal).tag
        }
    }

    // MARK: Content

    private var title: String {
        switch state.setupStep {
        case .welcome: "Welcome to Tidywise"
        case .model: "Which AI should do the reading?"
        case .folders: "Where should Tidywise tidy up?"
        case .topics: "Which topics fit you?"
        case .automation: "How independent should Tidywise be?"
        }
    }

    private var subtitle: String {
        switch state.setupStep {
        case .welcome: "Tidywise tidies loose files into topic folders – with an AI that runs only on your Mac."
        case .model: "The model recognizes what a file is about. It's downloaded once and then runs without internet."
        case .folders: "Choose which folders Tidywise looks in for loose files and where it sorts them to."
        case .topics: "Every topic becomes a folder. Deselect what you don't need – you can change everything later."
        case .automation: "Finally: what's allowed to happen in the background. You can change this at any time, too."
        }
    }

    @ViewBuilder private var content: some View {
        switch state.setupStep {
        case .welcome:
            WelcomeStep()
        case .model:
            ModelPicker(config: $draft, selectsImmediately: true)
        case .folders:
            WatchFoldersEditor(config: $draft)
            DestinationEditor(config: $draft)
        case .topics:
            TopicChooser(config: $draft, pool: $topicPool)
        case .automation:
            SettingsCard {
                SettingToggle(symbol: "clock.arrow.circlepath", title: "Check regularly",
                              on: "On: Tidywise checks for new files \(AppState.intervalText(minutes: draft.intervalMinutes)) while the app is open.",
                              off: "Off: Tidywise only checks when you click “Check for new files”.",
                              isOn: $automation)
                Divider()
                SettingToggle(symbol: "wand.and.stars", title: "Move automatically",
                              on: "On: Tidywise moves clear cases itself. Only unclear ones end up under “Review”.",
                              off: "Off (recommended to start): Tidywise only suggests, you confirm under “Review”.",
                              isOn: $draft.autoApply)
                Divider()
                SettingToggle(symbol: "link", title: "Browser history as a link collection",
                              on: "On: Pages you visit are collected by topic. Everything stays on your Mac.",
                              off: "Off: Tidywise doesn't read your browser history.",
                              isOn: $draft.linksEnabled)
                Divider()
                SettingToggle(symbol: "power", title: "Start at login",
                              on: "On: Tidywise starts with your Mac in the menu bar.",
                              off: "Off: Tidywise only works once you open the app.",
                              isOn: $launchAtLogin)
            }
            if automation || draft.linksEnabled { AccessCard(urgent: false) }
        }
    }

    // MARK: Footer

    private var footer: some View {
        HStack(spacing: 12) {
            if state.setupStep != .welcome {
                Button("Back") { move(-1) }
            } else if state.config.exists {
                Button("Cancel") { state.setupRequested = false }
            }
            Spacer()
            if let hint { Text(hint).font(.callout).foregroundStyle(.secondary).lineLimit(2).multilineTextAlignment(.trailing) }
            if state.setupStep == .automation {
                Button { state.finishSetup(draft, automation: automation, launchAtLogin: launchAtLogin) } label: { Text("Done").frame(minWidth: 90) }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(state.pendingSaves > 0 || state.finishingSetup)
            } else {
                Button { move(1) } label: { Text(state.setupStep == .welcome ? "Get started" : "Continue").frame(minWidth: 90) }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canContinue)
            }
        }
        .controlSize(.large)
        .padding(.horizontal, DS.pagePadding)
        .padding(.vertical, 14)
        .background(DS.surface)
    }

    private var canContinue: Bool {
        guard state.cliInstalled else { return false }
        switch state.setupStep {
        case .folders: return !draft.watch.isEmpty
        case .topics: return draft.topics.contains { !$0.isFallback }
        default: return true
        }
    }

    /// One sentence next to the button when something is missing or running alongside.
    private var hint: String? {
        guard state.cliInstalled else { return nil }
        switch state.setupStep {
        case .model:
            if let d = state.download, !d.failed { return "The download keeps running while you set things up." }
            if !state.isInstalled(draft.model) { return "Without a model, Tidywise can't sort anything yet – you can also download it later." }
            return nil
        case .folders: return draft.watch.isEmpty ? "Choose at least one folder." : nil
        case .topics: return canContinue ? nil : "Choose at least one topic."
        default: return nil
        }
    }

    private func move(_ delta: Int) {
        guard let next = SetupStep(rawValue: state.setupStep.rawValue + delta) else { return }
        withAnimation(.snappy(duration: 0.2)) { state.setupStep = next }
    }
}

/// Dots with labels: where you are and what's still to come.
private struct StepIndicator: View {
    let current: SetupStep

    var body: some View {
        HStack(spacing: 6) {
            ForEach(SetupStep.allCases) { step in
                let done = step.rawValue < current.rawValue
                let on = step == current
                HStack(spacing: 6) {
                    ZStack {
                        Circle().fill(on || done ? AnyShapeStyle(DS.accent.gradient) : AnyShapeStyle(DS.fill))
                        if done {
                            Image(systemName: "checkmark").font(.system(size: 9, weight: .bold)).foregroundStyle(.white)
                        } else {
                            Text("\(step.rawValue + 1)")
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(on ? Color.white : Color.secondary)
                        }
                    }
                    .frame(width: 20, height: 20)
                    Text(step.title)
                        .font(.callout.weight(on ? .semibold : .regular))
                        .foregroundStyle(on ? .primary : .secondary)
                }
                if step != SetupStep.allCases.last {
                    Capsule().fill(done ? DS.accent.opacity(0.6) : Color.primary.opacity(0.12)).frame(width: 26, height: 2)
                }
            }
        }
    }
}

private struct WelcomeStep: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            row("eye.fill", .blue, "Tidywise looks",
                "In the folders you're about to choose – and only for loose files. Folders, projects and apps stay as they are.")
            row("sparkles", DS.accent, "The AI reads along",
                "An AI model on your Mac recognizes what a file is about and suggests a topic.")
            row("checkmark", .green, "You decide",
                "You confirm the suggestions with one click. Every move can be undone.")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(padding: 22)

        HStack(alignment: .top, spacing: 12) {
            IconBadge(symbol: "lock.shield.fill", color: DS.ok, size: 34)
            VStack(alignment: .leading, spacing: 3) {
                Text("Everything stays on your Mac").font(.body.weight(.semibold))
                Text("No account, no cloud. Your files aren't sent anywhere – the AI runs locally. Tidywise needs the internet only once: to download the model.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(tint: DS.ok)
    }

    private func row(_ symbol: String, _ color: Color, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 14) {
            IconBadge(symbol: symbol, color: color, size: 38)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.body.weight(.semibold))
                Text(text).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// The app runs, but the part that sorts isn't installed – e.g. when only the app was copied.
private struct MissingInstallCard: View {
    @Environment(AppState.self) private var state

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if Backend.isBundled {
                Label("Python is missing", systemImage: "wrench.and.screwdriver.fill").font(.body.weight(.semibold))
                Text("The part of Tidywise that reads and moves files is written in Python, and this Mac has no Python 3.12 or newer yet. Install it once – Tidywise notices on its own and continues here.")
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Button("Download Python") { NSWorkspace.shared.open(URL(string: "https://www.python.org/downloads/macos/")!) }
                        .buttonStyle(.borderedProminent)
                    Text("or, with Homebrew:").font(.callout).foregroundStyle(.secondary)
                    Text("brew install python").font(.system(.callout, design: .monospaced)).textSelection(.enabled)
                    Button { state.copyToClipboard("brew install python") } label: { Label("Copy", systemImage: "doc.on.doc") }
                        .controlSize(.small)
                }
            } else {
                Label("Part of Tidywise is still missing", systemImage: "wrench.and.screwdriver.fill").font(.body.weight(.semibold))
                Text("The app is here, but the program that reads and moves the files isn't installed. Open Terminal in the project folder and start the installation – after that, things continue here on their own.")
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                HStack {
                    Text("./install.sh").font(.system(.callout, design: .monospaced)).textSelection(.enabled)
                    Spacer()
                    Button { state.copyToClipboard("./install.sh") } label: { Label("Copy", systemImage: "doc.on.doc") }.controlSize(.small)
                }
                .well()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(tint: DS.attention)
    }
}

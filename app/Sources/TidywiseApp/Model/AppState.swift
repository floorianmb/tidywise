import AppKit
import Foundation
import Observation
import ServiceManagement
import SwiftUI

enum Page: String, CaseIterable, Identifiable {
    case home = "Home"
    case review = "Review"
    case files = "Files"
    case links = "Links"
    case system = "System"
    case settings = "Settings"
    var id: String { rawValue }

    var icon: String {
        switch self {
        case .home: "house"
        case .review: "checklist"
        case .files: "folder"
        case .links: "link"
        case .system: "waveform.path.ecg"
        case .settings: "gearshape"
        }
    }
}

enum SettingsTab: String, CaseIterable, Identifiable {
    case folders = "Folders"
    case rules = "Rules"
    case topics = "Topics"
    case model = "AI model"
    case assistants = "Assistants"
    case general = "General"
    var id: String { rawValue }

    var icon: String {
        switch self {
        case .folders: "folder"
        case .rules: "slider.horizontal.3"
        case .topics: "square.grid.2x2"
        case .model: "cpu"
        case .assistants: "bubble.left.and.text.bubble.right"
        case .general: "gearshape"
        }
    }
}

/// The one sentence that says how things stand right now. Order = urgency.
enum Health {
    case starting, setup, paused, offline, noModel, working, needsReview, noService, ok
}

struct CommandResult: Equatable {
    let title: String
    let output: String
    let failed: Bool

    /// Last line of the output without the terminal hints – enough as a summary.
    var summary: String? {
        guard var line = output.split(separator: "\n").last.map(String.init) else { return nil }
        if let r = line.range(of: "Undo: tidywise") { line = String(line[..<r.lowerBound]) }
        line = line.trimmingCharacters(in: .whitespaces)
        return line.isEmpty ? nil : line
    }
}

@MainActor
@Observable
final class AppState {
    // Navigation (shared by the window and the menu bar)
    /// The start page can be chosen with `open -a Tidywise --args -page Files` (handy for testing)
    var page: Page = Page(rawValue: UserDefaults.standard.string(forKey: "page") ?? "") ?? .home
    var fileTopic: String?
    var selectedFileID: FileItem.ID?
    var linkTopic: String?
    var settingsTab: SettingsTab = .folders

    // Setup
    /// Show setup again even though a configuration already exists (from Settings).
    var setupRequested = false
    var setupStep: SetupStep = .welcome
    var cliInstalled = ConfigStore.cliInstalled

    // Configuration & data
    var config = TidywiseConfig()
    /// Only after the first read is it known whether setup has already been done.
    var configLoaded = false
    /// Saves in progress – until they finish, the configuration is not overwritten from disk.
    var pendingSaves = 0
    /// “Done” in setup was pressed and the last checks before saving are running.
    var finishingSetup = false
    var saveChain: Task<Void, Never>?
    var files: [FileItem] = []
    var links: [LinkItem] = []
    var searches: [SearchItem] = []
    var calls: [LLMCall] = []
    var meta: [String: String] = [:]

    // Live state
    var activity: Activity?
    var ollamaUp = false
    var ollamaVersion: String?
    var loadedModels: [LoadedModel] = []
    var installedModels: [InstalledModel] = []
    var download: ModelDownload?
    var downloadTask: Task<Void, Never>?
    /// Ollama is being started or installed right now.
    var ollamaBusy: String?
    /// The Python program macOS has to grant Full Disk Access to (asked from the CLI once).
    var helperPath: String?
    /// AI assistants on this Mac and whether they are connected (loaded when the settings page opens).
    var assistants: AssistantsStatus?
    var assistantBusy: String?
    var processes: [ProcessStat] = []
    var memoryUsed: Int64 = 0
    var memoryTotal: Int64 = Int64(ProcessInfo.processInfo.physicalMemory)
    var service = ServiceStatus()
    var logTail = ""

    // Commands from the app
    var runningCommand: String?
    var result: CommandResult?

    /// The one switch: off = nothing runs, the model uses no memory. Persists across restarts.
    var aiEnabled: Bool = UserDefaults.standard.object(forKey: "aiEnabled") as? Bool ?? true
    /// Currently turning off (stopping the run, unloading the model).
    var stopping = false
    /// When the app last kicked the service – if a run fails to happen, don't retry right away.
    var lastKick: Date?
    var launchAtLogin = SMAppService.mainApp.status == .enabled

    /// How often the app kicks off a run while it is open.
    var runInterval: TimeInterval { TimeInterval(max(1, config.intervalMinutes) * 60) }
    /// “every 15 minutes” / “every hour”
    var intervalText: String { Self.intervalText(minutes: config.intervalMinutes) }

    static func intervalText(minutes: Int) -> String {
        switch minutes {
        case 60: "every hour"
        case let m where m > 60 && m % 60 == 0: "every \(m / 60) hours"
        case let m: "every \(m) minutes"
        }
    }

    /// Only after the first check is it known whether Ollama is running – claim nothing until then.
    var ready = false
    private var started = false

    // MARK: Derived values

    var planned: [FileItem] { files.filter { $0.status == "planned" } }
    var moved: [FileItem] { files.filter { $0.status == "moved" } }
    var inbox: [FileItem] { moved.filter(\.isInInbox) }
    var isBusy: Bool { activity?.isAlive == true || runningCommand != nil }
    /// Setup replaces the main window until a configuration exists.
    var showSetup: Bool { setupRequested || (configLoaded && (!cliInstalled || !config.exists)) }
    /// Is the chosen model on disk? Ollama treats “name” and “name:latest” as the same.
    nonisolated static func modelMatches(_ a: String, _ b: String) -> Bool {
        let normalize = { (s: String) in s.contains(":") ? s : s + ":latest" }
        return normalize(a) == normalize(b)
    }

    var modelInstalled: Bool { isInstalled(config.model) }
    /// What runs the model, for sentences like “Ollama isn't running”.
    var serverName: String { config.usesOllama ? "Ollama" : "The model server" }
    func isInstalled(_ tag: String) -> Bool {
        installedModels.contains { Self.modelMatches($0.name, tag) }
    }
    /// May something that needs the AI be started right now?
    var canRunAI: Bool { aiEnabled && !stopping && !isBusy && !showSetup }
    /// Ready to invoke Ollama right now (AI on, Ollama reachable, model on disk).
    var canScan: Bool { canRunAI && ollamaUp && modelInstalled }
    var reviewCount: Int { planned.count + inbox.count }
    var movedThisWeek: Int {
        let limit = Date.now.addingTimeInterval(-7 * 24 * 3600)
        return moved.filter { ($0.movedAt ?? .distantPast) > limit }.count
    }

    var health: Health {
        if !ready || !configLoaded { return .starting }
        if showSetup { return .setup }
        if !aiEnabled { return .paused }
        if !ollamaUp { return .offline }
        if isBusy { return .working }
        if !modelInstalled { return .noModel }
        if reviewCount > 0 { return .needsReview }
        if !service.loaded { return .noService }
        return .ok
    }

    var healthTitle: String {
        switch health {
        case .starting: "One moment …"
        case .setup: "Not set up yet"
        case .paused: "AI is off"
        case .offline: "AI not reachable"
        case .noModel: "AI model missing"
        case .working: "Sorting now"
        case .needsReview: reviewCount == 1 ? "1 file to review" : "\(reviewCount) files to review"
        case .noService: "Automation not set up"
        case .ok: "All tidy"
        }
    }

    var healthColor: Color {
        switch health {
        case .starting, .paused: .gray
        case .offline: DS.problem
        case .working, .setup: DS.accent
        case .needsReview, .noService, .noModel: DS.attention
        case .ok: DS.ok
        }
    }
    var modelLoaded: LoadedModel? { loadedModels.first { Self.modelMatches($0.name, config.model) } ?? loadedModels.first }
    var modelRAM: Int64 { processes.reduce(0) { $0 + $1.rssBytes } }
    var lastRun: Date? { Format.date(meta["agent_last_run"]) }
    /// Tokens/s of the most recent calls, oldest first (for sparklines).
    var recentTPS: [Double] { Array(calls.prefix(24).compactMap(\.tokensPerSecond).reversed()) }
    var isWorking: Bool { activity?.isAlive == true }

    var exitCodeText: String {
        guard let raw = service.lastExit else { return "–" }
        if raw.contains("never") { return "Never finished" }
        if let code = Int(raw.prefix { $0.isNumber || $0 == "-" }) {
            return code == 0 ? "OK" : "Error (\(raw))"
        }
        return raw
    }

    /// Next automatic run – nil when AI is off or the service is missing.
    var nextRun: Date? {
        guard aiEnabled, service.loaded else { return nil }
        guard let base = [lastRun, lastKick].compactMap({ $0 }).max() else { return .now }
        return base.addingTimeInterval(runInterval)
    }

    var nextRunText: String {
        if activity?.isAlive == true { return "running now" }
        if !aiEnabled { return "when AI is on" }
        guard let next = nextRun else { return "–" }
        return next < .now.addingTimeInterval(30) ? "any moment" : Format.relative(next)
    }
    var lastCall: LLMCall? { calls.first { $0.ok && $0.tokensPerSecond != nil } }

    var averageTokensPerSecond: Double? {
        let v = calls.prefix(30).compactMap(\.tokensPerSecond)
        return v.isEmpty ? nil : v.reduce(0, +) / Double(v.count)
    }

    /// Output of the most recent background run (each run starts with a `--- YYYY-MM-DD …` line).
    private var lastLogBlock: String {
        if let range = logTail.range(of: "\n--- ", options: .backwards) {
            return String(logTail[range.upperBound...])
        }
        return logTail
    }

    /// Blocked if the last run hit a Chrome permission error and no successful Chrome sync happened since.
    var chromeBlocked: Bool {
        if activity?.isAlive == true && activity?.task == "Sorting history" { return false }
        guard config.linksEnabled else { return false }
        if let lastSync = Format.date(meta["chrome_last_run"]), let lastRun = lastRun, lastSync >= lastRun.addingTimeInterval(-60) {
            return false
        }
        // The run logs “Chrome history: [Errno 1] Operation not permitted: …” – other files' errors don't count.
        return lastLogBlock.split(separator: "\n").contains {
            $0.contains("Chrome history:") && $0.contains("Operation not permitted")
        }
    }

    /// macOS denied the background service access to a folder during the last run.
    var foldersBlocked: Bool {
        lastLogBlock.contains("No access to")
    }
    var accessBlocked: Bool { chromeBlocked || foldersBlocked }

    // MARK: Refreshing

    func start() {
        guard !started else { return }
        started = true
        Task { await refreshAll() }
        Task {
            while true {
                try? await Task.sleep(for: .seconds(1))
                await refreshLive()
            }
        }
        Task {
            while true {
                try? await Task.sleep(for: .seconds(4))
                await refreshData()
            }
        }
        // Clock: while the app is running, kick off a run at the configured interval.
        Task {
            try? await Task.sleep(for: .seconds(5))
            while true {
                scheduleTick()
                try? await Task.sleep(for: .seconds(30))
            }
        }
    }

    private func scheduleTick() {
        guard Snapshot.directory == nil, ready, ollamaUp, modelInstalled, canRunAI, let next = nextRun, next <= .now else { return }
        runServiceNow()
    }

    func refreshAll() async {
        await refreshLive()
        await refreshData()
    }

    func refreshLive() async {
        let ollama = Ollama(baseURL: config.ollamaURL)
        let usesOllama = config.usesOllama
        let server = LocalServer(baseURL: config.serverURL)
        async let models = usesOllama ? ollama.loadedModels() : nil
        async let onDisk = usesOllama ? ollama.installedModels()
                                      : await server.modelIDs()?.map { InstalledModel(name: $0, size: 0) }
        let (act, procs, mem) = await Task.detached {
            (Sources.readActivity(), Sources.ollamaProcesses(), Sources.systemMemory())
        }.value
        let installed = await onDisk
        // Another server: reachable = it lists its models; which one is loaded is its business.
        let loaded = usesOllama ? await models : (installed == nil ? nil : [])
        if let installed { installedModels = installed } else if !usesOllama { installedModels = [] }
        let wasBusy = activity?.isAlive == true
        activity = act
        processes = procs
        memoryUsed = mem.used
        memoryTotal = mem.total
        ollamaUp = loaded != nil
        loadedModels = loaded ?? []
        ready = true
        if usesOllama, ollamaVersion == nil, ollamaUp { ollamaVersion = await ollama.version() }
        // As soon as a run ends, fetch fresh data right away
        if wasBusy && act?.isAlive != true { await refreshData() }
    }

    func refreshData() async {
        let live = Snapshot.directory == nil
        let (snap, svc, log, cfg) = await Task.detached {
            // Downloaded app: (re)write the launcher and refresh launchd if the app moved or Python changed.
            if Backend.isBundled, live, let python = Backend.prepare() {
                Backend.refreshServiceIfNeeded(python: python)
            }
            return (Sources.readDatabase(), Sources.serviceStatus(), Sources.logTail(), ConfigStore.load())
        }.value
        files = snap.files
        links = snap.links
        searches = snap.searches
        calls = snap.calls
        meta = snap.meta
        service = svc
        logTail = log
        cliInstalled = ConfigStore.cliInstalled
        if pendingSaves == 0 { apply(cfg) }
        configLoaded = true
        if helperPath == nil, cliInstalled { helperPath = await Task.detached { Sources.helperPath() }.value }
    }

    /// Applies a configuration that was read – including the topics' colors and symbols.
    func apply(_ cfg: TidywiseConfig) {
        guard cfg != config || TopicStyle.isEmpty else { return }
        config = cfg
        TopicStyle.register(cfg.topics)
    }

    // MARK: Actions

    /// After a successful run, `summary` replaces the CLI output with one sentence for the app.
    func runCLI(_ title: String, _ args: [String], needsAI: Bool = false, summary: (@MainActor () -> String)? = nil) {
        guard runningCommand == nil, !needsAI || canRunAI else { return }
        runningCommand = title
        result = nil
        Task {
            let r = await Shell.runAsync(Paths.cli.path, args)
            await refreshData()
            runningCommand = nil
            if needsAI, !r.ok, !aiEnabled {
                result = CommandResult(title: title, output: "Cancelled – AI was turned off.", failed: false)
            } else if r.ok, let summary {
                result = CommandResult(title: title, output: summary(), failed: false)
            } else {
                result = CommandResult(title: title, output: r.output.trimmingCharacters(in: .whitespacesAndNewlines), failed: !r.ok)
            }
        }
    }

    func scan() {
        // The CLI output ends with hints for the terminal – in the app, what matters is what to do next.
        runCLI("Check for new files", ["scan", "-q"], needsAI: true) { [self] in
            switch planned.count {
            case 0: "Nothing new found."
            case 1: "1 suggestion is waiting under “Review”."
            default: "\(planned.count) suggestions are waiting under “Review”."
            }
        }
    }
    func syncLinks() { runCLI("Read Chrome history", ["links", "-q"], needsAI: true) }
    func openSettings(_ tab: SettingsTab) {
        settingsTab = tab
        page = .settings
    }
    func applyAll() { runCLI("Move suggestions", ["apply"]) }
    func apply(_ ids: [Int]) {
        runCLI(ids.count == 1 ? "Move file" : "Move \(ids.count) files", ["apply"] + ids.map(String.init))
    }
    func setTopic(_ file: FileItem, _ slug: String) { runCLI("\(file.name) → \(config.topicName(slug))", ["set", "\(file.id)", slug]) }
    func undo(_ file: FileItem) { runCLI("Put back \(file.name)", ["undo", "\(file.id)"]) }

    // MARK: Navigation

    func showFiles(topic: String?) {
        fileTopic = topic
        page = .files
    }

    func showFile(_ file: FileItem) {
        fileTopic = nil
        selectedFileID = file.id
        page = .files
    }

    func showLinks(topic: String?) {
        linkTopic = topic
        page = .links
    }

    func runServiceNow() {
        guard canRunAI else { return }
        lastKick = .now
        Task {
            _ = await Shell.runAsync("/bin/launchctl", ["kickstart", "gui/\(getuid())/\(Paths.serviceLabel)"])
            try? await Task.sleep(for: .seconds(1))
            await refreshAll()
        }
    }

    func setAutoApply(_ on: Bool) { updateConfig { $0.autoApply = on } }

    func loadModel() {
        guard config.usesOllama else { return }
        let ollama = Ollama(baseURL: config.ollamaURL)
        let model = config.model
        runningCommand = "Load model"
        Task {
            await ollama.setKeepAlive(model: model, keepAlive: "5m")
            runningCommand = nil
            await refreshLive()
        }
    }

    func unloadModel() {
        guard config.usesOllama else { return }
        let ollama = Ollama(baseURL: config.ollamaURL)
        let model = modelLoaded?.name ?? config.model
        Task {
            await ollama.setKeepAlive(model: model, keepAlive: "0")
            await refreshLive()
        }
    }

    // MARK: AI on/off

    func setAIEnabled(_ on: Bool) {
        guard on != aiEnabled else { return }
        aiEnabled = on
        UserDefaults.standard.set(on, forKey: "aiEnabled")
        if on {
            // Check right away in case a run is due
            Task { await refreshAll(); scheduleTick() }
        } else {
            stopping = true
            Task {
                await Task.detached { Self.stopRunsAndUnload(timeout: 10) }.value
                stopping = false
                await refreshAll()
            }
        }
    }

    /// Stops any runs in progress (the service and commands from the app) and unloads the model from memory.
    /// Synchronous, so that it still completes fully when the app quits.
    nonisolated static func stopRunsAndUnload(timeout: TimeInterval) {
        _ = Shell.run("/bin/launchctl", ["kill", "SIGTERM", "gui/\(getuid())/\(Paths.serviceLabel)"])
        if let a = Sources.readActivity(), a.isAlive, let pid = a.pid { kill(pid_t(pid), SIGTERM) }
        // Wait briefly until the run is gone – otherwise its last request loads the model right back in.
        let deadline = Date.now.addingTimeInterval(3)
        while Sources.readActivity()?.isAlive == true, Date.now < deadline { usleep(100_000) }

        let config = ConfigStore.last
        guard config.usesOllama else { return }  // another server manages its memory itself
        let names = Ollama.loadedModelNamesSync(baseURL: config.ollamaURL, timeout: 2)
        for name in names where Self.modelMatches(name, config.model) || names.count == 1 {
            Ollama.unloadSync(baseURL: config.ollamaURL, model: name, timeout: timeout)
        }
    }

    func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            result = CommandResult(title: "Start at login", output: "macOS didn't allow that: \(error.localizedDescription)", failed: true)
        }
        launchAtLogin = SMAppService.mainApp.status == .enabled
        if on, SMAppService.mainApp.status == .requiresApproval {
            result = CommandResult(title: "Start at login", output: "Please allow it in System Settings under “Login Items”.", failed: false)
            SMAppService.openSystemSettingsLoginItems()
        }
    }

    func openRoot() {
        guard FileManager.default.fileExists(atPath: config.rootPath) else {
            result = CommandResult(title: "Destination folder",
                                   output: "“\(Paths.short(config.rootPath))” hasn't been created yet – it appears as soon as Tidywise sorts the first file.",
                                   failed: false)
            return
        }
        NSWorkspace.shared.open(config.root)
    }
    func reveal(_ file: FileItem) { NSWorkspace.shared.activateFileViewerSelecting([file.currentURL]) }
    func open(_ file: FileItem) { NSWorkspace.shared.open(file.currentURL) }

    func openFullDiskAccessSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") {
            NSWorkspace.shared.open(url)
        }
    }

    func copyToClipboard(_ s: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(s, forType: .string)
    }
}

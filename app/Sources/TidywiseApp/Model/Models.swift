import Foundation

struct Topic: Identifiable, Hashable, Codable {
    /// Folder name under Topics/. Empty for a new topic – the CLI derives it from the name when saving.
    var slug: String
    var name: String
    var description = ""
    var symbol = "folder.fill"
    var color = "blue"
    var id: String { slug }
    /// The catch-all topic. Installs set up before the switch to English call it "sonstiges".
    var isFallback: Bool { slug == "other" || slug == "sonstiges" }
}

/// The settings as `tidywise config --as-json` returns them. Reading and writing only goes through the CLI
/// (see `ConfigStore`) – the app doesn't know the file format.
struct TidywiseConfig: Codable, Equatable {
    /// false as long as no configuration file exists yet – then the app shows setup.
    var exists = false
    var rootPath = Paths.home.appending(path: "Downloads/Tidywise").path
    var watch = ["Downloads", "Documents", "Desktop"].map { Paths.home.appending(path: $0).path }
    var model = ModelCatalog.standard.tag
    var ollamaURL = "http://127.0.0.1:11434"
    /// "ollama", or "openai" for another local server with an OpenAI-style API (LM Studio, mlx-lm, llama.cpp).
    /// Optional so that a CLI without these settings still decodes; nil is not sent, so the CLI keeps its value.
    var llmBackend: String?
    var llmURL: String?
    var profile = ""
    var summaryLanguage = "English"
    var minAgeMinutes: Double = 60
    var minConfidence: Double = 0.6
    var autoApply = false
    var subfolders = "none"
    var intervalMinutes = 15
    var linksEnabled = false
    /// Browsers whose history is read (chrome, safari, firefox, arc, brave, vivaldi, edge). Optional so that
    /// a CLI without this setting still decodes; nil is not sent, so the CLI keeps its value.
    var browsers: [String]?
    var chromeProfile = "Default"
    var linksFirstDays = 14
    var linksIntervalMinutes = 60
    var mcpEnabled = false
    var skipExtensions: [String] = []
    var topics: [Topic] = []
    /// Names of the folders inside the destination ("topics", "days", "private", "inbox", "links").
    /// Older installs keep their German names (Themen, Tage, _privat).
    var folderNames: [String: String] = [:]

    enum CodingKeys: String, CodingKey {
        case exists, watch, model, profile, subfolders, topics
        case folderNames = "folder_names"
        case summaryLanguage = "summary_language"
        case mcpEnabled = "mcp_enabled"
        case rootPath = "root"
        case ollamaURL = "ollama_url"
        case llmBackend = "llm_backend"
        case llmURL = "llm_url"
        case minAgeMinutes = "min_age_minutes"
        case minConfidence = "min_confidence"
        case autoApply = "auto_apply"
        case intervalMinutes = "interval_minutes"
        case linksEnabled = "links_enabled"
        case chromeProfile = "chrome_profile"
        case browsers
        case linksFirstDays = "links_first_days"
        case linksIntervalMinutes = "links_interval_minutes"
        case skipExtensions = "skip_extensions"
    }

    var root: URL { URL(fileURLWithPath: rootPath) }

    var chosenBrowsers: [String] { browsers ?? ["chrome"] }
    /// “Chrome”, “Safari and Firefox” – for sentences in the interface.
    var browserNames: String {
        let names = Browser.all.filter { chosenBrowsers.contains($0.id) }.map(\.title)
        switch names.count {
        case 0: return "browser"
        case 1: return names[0]
        default: return names.dropLast().joined(separator: ", ") + " and " + names.last!
        }
    }

    static let defaultServerURL = "http://127.0.0.1:1234/v1"
    /// Does the model run through Ollama (the default) – or through another local server?
    var usesOllama: Bool { (llmBackend ?? "ollama") == "ollama" }
    var serverURL: String { llmURL ?? Self.defaultServerURL }

    func folderName(_ key: String, default name: String) -> String { folderNames[key] ?? name }

    func topicName(_ slug: String?) -> String {
        guard let slug else { return "–" }
        return topics.first { $0.slug == slug }?.name ?? slug
    }

    /// “Downloads, Documents and Desktop” – for sentences in the interface.
    var watchNames: String {
        let names = watch.map(Paths.folderName(ofFolder:))
        switch names.count {
        case 0: return "no folder"
        case 1: return names[0]
        default: return names.dropLast().joined(separator: ", ") + " and " + names.last!
        }
    }

    /// “an hour”, “10 minutes” – how long a file has to sit before Tidywise touches it.
    var minAgeText: String { WaitChoice.text(minutes: minAgeMinutes) }
}

/// Selectable waiting times, in words instead of minutes.
enum WaitChoice {
    static let all: [(minutes: Double, title: String)] = [
        (0, "Right away"), (10, "After 10 minutes"), (60, "After 1 hour"), (24 * 60, "After 1 day"), (7 * 24 * 60, "After 1 week"),
    ]

    static func text(minutes: Double) -> String {
        switch minutes {
        case ..<1: return "right away"
        case 60: return "an hour"
        case 24 * 60: return "a day"
        case 7 * 24 * 60: return "a week"
        case ..<60: return "\(Int(minutes)) minutes"
        case ..<(24 * 60): return "\(Format.number(minutes / 60, digits: minutes.truncatingRemainder(dividingBy: 60) == 0 ? 0 : 1)) hours"
        default: return "\(Format.number(minutes / 1440, digits: 0)) days"
        }
    }
}

struct FileItem: Identifiable, Hashable {
    let id: Int
    let name: String
    let topic: String?
    let newTopic: String?
    let status: String
    let summary: String
    let keywords: String
    let sourceURL: String?
    let confidence: Double
    let origPath: String
    let curPath: String?
    let movedAt: Date?
    let classifiedAt: Date?

    var isInInbox: Bool {
        guard let curPath else { return false }
        return URL(fileURLWithPath: curPath).deletingLastPathComponent().lastPathComponent == "_inbox"
    }
    var currentURL: URL { URL(fileURLWithPath: curPath ?? origPath) }
    var ext: String { (name as NSString).pathExtension.lowercased() }
}

struct LinkItem: Identifiable, Hashable {
    var id: String { url }
    let url: String
    let title: String
    let domain: String
    let topic: String?
    let lastVisit: Date?
    let visits: Int
}

struct SearchItem: Identifiable, Hashable {
    var id: String { term + (at.map { "\($0.timeIntervalSince1970)" } ?? "") }
    let term: String
    let at: Date?
}

struct LLMCall: Identifiable, Hashable {
    let id: Int
    let at: Date
    let model: String
    let kind: String
    let label: String
    let promptTokens: Int
    let outputTokens: Int
    let promptMs: Double
    let evalMs: Double
    let loadMs: Double
    let totalMs: Double
    let images: Int
    let ok: Bool
    let error: String?

    var tokensPerSecond: Double? { evalMs > 0 && outputTokens > 0 ? Double(outputTokens) / evalMs * 1000 : nil }
    var promptTokensPerSecond: Double? { promptMs > 0 && promptTokens > 0 ? Double(promptTokens) / promptMs * 1000 : nil }
    var kindLabel: String {
        switch kind {
        case "file", "datei": return "File"
        case "links": return "Links"
        default: return kind.isEmpty ? "–" : kind
        }
    }
}

struct Activity: Decodable, Equatable {
    var busy: Bool
    var pid: Int?
    var task: String?
    var label: String?
    var done: Int?
    var total: Int?
    var started_at: String?
    var updated_at: String?
    var last_finished: String?

    var progress: Double? {
        guard let done, let total, total > 0 else { return nil }
        return Double(done) / Double(total)
    }

    /// A process that crashed leaves busy=true behind – then it counts as finished.
    var isAlive: Bool {
        guard busy, let pid else { return false }
        return kill(pid_t(pid), 0) == 0
    }
}

struct LoadedModel: Decodable, Identifiable, Hashable {
    let name: String
    let size: Int64
    let size_vram: Int64?
    let expires_at: String?
    let context_length: Int?
    var id: String { name }
    var expiresAt: Date? { Format.date(expires_at) }
}

/// What `tidywise mcp status --as-json` reports: which AI assistants are on this Mac and whether they are connected.
struct AssistantsStatus: Decodable, Equatable {
    struct Client: Decodable, Identifiable, Equatable {
        let id: String
        let title: String
        let installed: Bool
        let connected: Bool
    }

    /// The command an assistant runs to start the server.
    let commandLine: String
    /// Configuration in the common `mcpServers` format, for assistants Tidywise has no button for.
    let snippet: String
    let clients: [Client]

    enum CodingKeys: String, CodingKey {
        case commandLine = "command_line"
        case snippet, clients
    }
}

/// A model that Ollama has on disk (it doesn't have to be loaded).
struct InstalledModel: Decodable, Identifiable, Hashable {
    let name: String
    let size: Int64
    var id: String { name }
}

/// Progress of a model download.
struct ModelDownload: Equatable {
    var model: String
    var status = "Connecting …"
    var completed: Int64 = 0
    var total: Int64 = 0
    var error: String?

    var fraction: Double? { total > 0 ? min(1, Double(completed) / Double(total)) : nil }
    var failed: Bool { error != nil }
    var detail: String {
        if let error { return error }
        guard total > 0 else { return status }
        return "\(Format.fileSize(completed)) of \(Format.fileSize(total))"
    }
}

struct ProcessStat: Identifiable, Hashable {
    let pid: Int
    let rssBytes: Int64
    let cpu: Double
    let command: String
    var id: Int { pid }
    var displayName: String {
        if command.contains("llama-server") || command.contains("runner") { return "Model process" }
        if command.hasSuffix("ollama") || command.contains("ollama serve") { return "Ollama server" }
        return (command as NSString).lastPathComponent
    }
}

struct ServiceStatus: Equatable {
    var loaded = false
    var running = false
    var runs: Int?
    var lastExit: String?
}

/// A browser whose history Tidywise can read (the CLI knows where each keeps it: browsers.py).
struct Browser: Identifiable {
    let id: String
    let title: String
    let app: String

    static let all = [
        Browser(id: "chrome", title: "Chrome", app: "Google Chrome.app"),
        Browser(id: "safari", title: "Safari", app: "Safari.app"),
        Browser(id: "firefox", title: "Firefox", app: "Firefox.app"),
        Browser(id: "arc", title: "Arc", app: "Arc.app"),
        Browser(id: "brave", title: "Brave", app: "Brave Browser.app"),
        Browser(id: "vivaldi", title: "Vivaldi", app: "Vivaldi.app"),
        Browser(id: "edge", title: "Edge", app: "Microsoft Edge.app"),
    ]

    var isInstalled: Bool {
        ["/Applications", Paths.home.appending(path: "Applications").path, "/System/Applications", "/System/Cryptexes/App/System/Applications"]
            .contains { FileManager.default.fileExists(atPath: "\($0)/\(app)") }
    }
}

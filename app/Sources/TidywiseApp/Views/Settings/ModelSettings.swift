import SwiftUI

/// State of Ollama (the program that runs the model) with the one button that helps you move on.
struct OllamaStatusRow: View {
    @Environment(AppState.self) private var state

    var body: some View {
        HStack(spacing: 12) {
            if state.ollamaBusy != nil {
                ProgressView().controlSize(.small).frame(width: 30, height: 30)
            } else {
                IconBadge(symbol: state.ollamaUp ? "checkmark" : (OllamaSetup.isInstalled ? "bolt.slash.fill" : "arrow.down.to.line"),
                          color: state.ollamaUp ? DS.ok : DS.attention, size: 30, round: true)
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.body.weight(.medium))
                Text(detail).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 12)
            if state.ollamaBusy == nil, !state.ollamaUp {
                if OllamaSetup.isInstalled {
                    Button("Start Ollama") { state.startOllama() }.buttonStyle(.borderedProminent)
                } else {
                    if OllamaSetup.brewPath != nil {
                        Button("Install with Homebrew") { state.installOllamaWithBrew() }
                    }
                    Button("Download Ollama") { state.openOllamaDownloadPage() }.buttonStyle(.borderedProminent)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(padding: 14, tint: state.ollamaUp ? nil : DS.attention)
    }

    private var title: String {
        if let busy = state.ollamaBusy { return busy }
        if state.ollamaUp { return "Ollama is running" }
        return OllamaSetup.isInstalled ? "Ollama isn't running" : "Ollama isn't installed yet"
    }

    private var detail: String {
        if state.ollamaBusy != nil { return "One moment." }
        if state.ollamaUp {
            return "The program that runs the AI on your Mac" + (state.ollamaVersion.map { " · version \($0)" } ?? "") + "."
        }
        if OllamaSetup.isInstalled { return "Ollama runs the AI on your Mac. It's installed but not running right now." }
        return "Ollama is a free program that runs AI models locally on your Mac. Once it's installed, you can continue here."
    }
}

/// Choose a model and download it with one click.
struct ModelPicker: View {
    @Environment(AppState.self) private var state
    @Binding var config: TidywiseConfig
    /// In setup the choice applies immediately (the draft remembers it, the download runs alongside).
    /// In Settings the switch only happens once the model is there – otherwise Tidywise would be left without a model.
    var selectsImmediately = false
    @ViewState private var custom = ""

    private var recommended: ModelOption { ModelCatalog.recommended(ramBytes: state.memoryTotal) }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.gap) {
            // Setup keeps it simple; another server can be chosen in Settings afterwards.
            if !selectsImmediately { BackendChoice(config: $config) }

            if config.usesOllama {
                OllamaStatusRow()

                HStack(alignment: .top, spacing: 12) {
                    ForEach(ModelCatalog.all) { card($0) }
                }
                .fixedSize(horizontal: false, vertical: true)

                if let d = state.download, d.failed {
                    Notice(text: "Downloading \(d.model) didn't work: \(d.detail)", actionTitle: "Close") { state.download = nil }
                }

                others
            } else {
                LocalServerSettings(config: $config)
            }
        }
        // Switching the server or its address: look right away what is there now.
        .task(id: "\(state.config.usesOllama) \(state.config.serverURL)") { await state.refreshLive() }
    }

    // MARK: Cards

    private func card(_ option: ModelOption) -> some View {
        let active = config.model == option.tag
        let installed = state.isInstalled(option.tag)
        let ramGB = Int((Double(state.memoryTotal) / 1_073_741_824).rounded())
        let tight = ramGB < option.comfortableRAMGB
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text(option.title).font(DS.sectionFont)
                if option == recommended {
                    Text("Recommended")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(DS.accent, in: Capsule())
                }
                Spacer(minLength: 0)
                if active { Image(systemName: "checkmark.circle.fill").foregroundStyle(DS.accent).font(.system(size: 17)) }
            }
            Text(option.family).font(.caption.weight(.medium)).foregroundStyle(.secondary)
            Text(option.text).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 3) {
                Label("\(Format.fileSize(option.downloadBytes)) download", systemImage: "internaldrive")
                Label(tight ? "Better with \(option.comfortableRAMGB) GB of memory or more – your Mac has \(ramGB) GB" : "Fits your \(ramGB) GB of memory well",
                      systemImage: tight ? "exclamationmark.triangle" : "memorychip")
                    .foregroundStyle(tight ? AnyShapeStyle(DS.attention) : AnyShapeStyle(.secondary))
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            Spacer(minLength: 4)
            control(option.tag, size: option.downloadBytes, active: active, installed: installed)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .card(padding: 16)
        .overlay(RoundedRectangle(cornerRadius: DS.radius, style: .continuous).strokeBorder(active ? DS.accent : .clear, lineWidth: 1.5))
    }

    /// The one button per model: download, use – or the progress.
    @ViewBuilder private func control(_ tag: String, size: Int64?, active: Bool, installed: Bool) -> some View {
        if let d = state.download, d.model == tag, !d.failed {
            VStack(alignment: .leading, spacing: 6) {
                ProgressBar(value: d.fraction)
                HStack {
                    Text(d.detail).font(.caption.monospacedDigit()).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    Button("Cancel") { state.cancelDownload() }.buttonStyle(.link).font(.caption)
                }
            }
        } else if installed {
            if active {
                Label("In use", systemImage: "checkmark").font(.callout.weight(.medium)).foregroundStyle(DS.ok)
            } else {
                Button("Use") { config.model = tag }
            }
        } else {
            Button {
                if selectsImmediately { config.model = tag }
                state.downloadModel(tag, select: !selectsImmediately)
            } label: {
                Label(size.map { "Download · \(Format.fileSize($0))" } ?? "Download", systemImage: "arrow.down.circle")
            }
            .buttonStyle(.borderedProminent)
            .disabled(!state.ollamaUp || state.downloadTask != nil)
            .help(state.ollamaUp ? "Downloads the model to your Mac through Ollama" : "Ollama needs to be running for this")
        }
    }

    // MARK: Other models

    private var others: some View {
        let known = ModelCatalog.all.map(\.tag)
        let isKnown = { (tag: String) in known.contains { AppState.modelMatches($0, tag) } }
        let extra = state.installedModels.filter { !isKnown($0.name) }
        return Collapsible("Another model", detail: otherDetail(extra.count), systemImage: "shippingbox",
                           startsOpen: !isKnown(config.model)) {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(extra) { m in
                    HStack(spacing: 10) {
                        Image(systemName: "cpu").foregroundStyle(.secondary).frame(width: 20)
                        Text(m.name).font(.system(.callout, design: .monospaced))
                        Text(Format.fileSize(m.size)).font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        control(m.name, size: nil, active: AppState.modelMatches(config.model, m.name), installed: true)
                    }
                }
                if !isKnown(config.model), !state.isInstalled(config.model) {
                    HStack(spacing: 10) {
                        Image(systemName: "cpu").foregroundStyle(.secondary).frame(width: 20)
                        Text(config.model).font(.system(.callout, design: .monospaced))
                        Text("selected, but not downloaded yet").font(.caption).foregroundStyle(DS.attention)
                        Spacer()
                        control(config.model, size: nil, active: true, installed: false)
                    }
                }
                HStack(spacing: 8) {
                    TextField("Name as on Ollama, e.g. llama3.2-vision", text: $custom)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit(loadCustom)
                    Button("Download", action: loadCustom)
                        .disabled(custom.trimmingCharacters(in: .whitespaces).isEmpty || !state.ollamaUp || state.downloadTask != nil)
                }
                if let d = state.download, !d.failed, !isKnown(d.model),
                   !extra.contains(where: { AppState.modelMatches($0.name, d.model) }), !AppState.modelMatches(d.model, config.model) {
                    control(d.model, size: nil, active: false, installed: false)
                }
                Text("Any model from the Ollama library works. Ideally one that understands images – then Tidywise also recognizes photos and scanned PDFs.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func otherDetail(_ count: Int) -> String {
        switch count {
        case 0: "For experts: any model from the Ollama library"
        case 1: "1 more model is already on your Mac"
        default: "\(count) more models are already on your Mac"
        }
    }

    private func loadCustom() {
        let tag = custom.trimmingCharacters(in: .whitespaces)
        guard !tag.isEmpty else { return }
        if state.isInstalled(tag) {
            config.model = tag
        } else {
            if selectsImmediately { config.model = tag }
            state.downloadModel(tag, select: !selectsImmediately)
        }
        custom = ""
    }
}

/// Ollama (the default, with one-click downloads) or another local server the user already runs.
struct BackendChoice: View {
    @Binding var config: TidywiseConfig

    private var backend: Binding<String> {
        Binding(get: { config.usesOllama ? "ollama" : "openai" }, set: { value in
            var next = config
            next.llmBackend = value
            if value == "openai", next.llmURL == nil { next.llmURL = TidywiseConfig.defaultServerURL }
            config = next
        })
    }

    var body: some View {
        SettingsCard {
            SettingRow("server.rack", "Runs on",
                       "Ollama is the easiest: models download here with one click. Already using LM Studio, MLX or llama.cpp? Tidywise can use that instead.") {
                Picker("Runs on", selection: backend) {
                    Text("Ollama").tag("ollama")
                    Text("Another local server").tag("openai")
                }
                .labelsHidden()
                .fixedSize()
            }
        }
    }
}

/// A local server with an OpenAI-style API (LM Studio, mlx-lm, llama.cpp): its address and which of its models to use.
struct LocalServerSettings: View {
    @Environment(AppState.self) private var state
    @Binding var config: TidywiseConfig
    @ViewState private var url = ""

    var body: some View {
        VStack(alignment: .leading, spacing: DS.gap) {
            status
            address
            models
        }
        .onAppear { url = config.serverURL }
        .onChange(of: config.serverURL) { url = config.serverURL }
    }

    private var status: some View {
        HStack(spacing: 12) {
            IconBadge(symbol: state.ollamaUp ? "checkmark" : "bolt.slash.fill",
                      color: state.ollamaUp ? DS.ok : DS.attention, size: 30, round: true)
            VStack(alignment: .leading, spacing: 1) {
                Text(state.ollamaUp ? "The model server is running" : "No model server answers").font(.body.weight(.medium))
                Text(state.ollamaUp
                     ? "\(config.serverURL) · \(state.installedModels.count == 1 ? "1 model" : "\(state.installedModels.count) models")"
                     : "Start the local server in LM Studio (Developer → Start Server), or run mlx_lm.server or llama-server.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 12)
            Button("Check again") { Task { await state.refreshLive() } }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(padding: 14, tint: state.ollamaUp ? nil : DS.attention)
    }

    private var address: some View {
        VStack(alignment: .leading, spacing: 10) {
            SettingLabel(symbol: "network", title: "Address",
                         text: "Including /v1. LM Studio: http://127.0.0.1:1234/v1 · mlx-lm and llama.cpp: http://127.0.0.1:8080/v1. Only servers on this Mac – your files don't leave it.")
            HStack(spacing: 8) {
                TextField(TidywiseConfig.defaultServerURL, text: $url)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.callout, design: .monospaced))
                    .onSubmit(commit)
                if trimmed != config.serverURL {
                    Button("Save", action: commit).buttonStyle(.borderedProminent)
                }
            }
            .padding(.leading, 36)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private var models: some View {
        VStack(alignment: .leading, spacing: 10) {
            CardTitle(title: "Model", systemImage: "cpu")
            ForEach(state.installedModels) { m in
                HStack(spacing: 10) {
                    Image(systemName: "cpu").foregroundStyle(.secondary).frame(width: 20)
                    Text(m.name).font(.system(.callout, design: .monospaced)).lineLimit(1).truncationMode(.middle)
                    Spacer()
                    if AppState.modelMatches(config.model, m.name) {
                        Label("In use", systemImage: "checkmark").font(.callout.weight(.medium)).foregroundStyle(DS.ok)
                    } else {
                        Button("Use") { config.model = m.name }
                    }
                }
            }
            if state.ollamaUp, !state.modelInstalled {
                Text("“\(config.model)” isn't offered by this server. Pick one of its models\(state.installedModels.isEmpty ? " once it has loaded some" : " above").")
                    .font(.callout).foregroundStyle(DS.attention).fixedSize(horizontal: false, vertical: true)
            } else if !state.ollamaUp {
                Text("The models show up here as soon as the server answers.").font(.callout).foregroundStyle(.secondary)
            }
            Text("Ideally a model that understands images – then Tidywise also recognizes photos and scanned PDFs.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private var trimmed: String {
        url.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    private func commit() {
        let value = trimmed
        guard !value.isEmpty, value != config.serverURL else { return }
        config.llmURL = value
    }
}

/// One sentence about the person – helps the model sort.
struct ProfileField: View {
    @Binding var config: TidywiseConfig
    @ViewState private var text = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SettingLabel(symbol: "person.fill", title: "About you (optional)",
                         text: "One sentence helps the AI sort – for example what you do for work or what you're working on right now. It stays on your Mac.")
            HStack(spacing: 8) {
                TextField("e.g. “Architecture student, working part-time at a café”", text: $text)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(commit)
                if text.trimmingCharacters(in: .whitespaces) != config.profile {
                    Button("Save", action: commit).buttonStyle(.borderedProminent)
                }
            }
            .padding(.leading, 36)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
        .onAppear { text = config.profile }
        .onChange(of: config.profile) { text = config.profile }
        .onDisappear(perform: commit)
    }

    private func commit() {
        let value = text.trimmingCharacters(in: .whitespaces)
        if value != config.profile { config.profile = value }
    }
}

/// The language summaries and keywords are written in.
struct SummaryLanguageSetting: View {
    @Binding var config: TidywiseConfig

    private static let languages = ["English", "German", "French", "Spanish", "Italian", "Portuguese", "Dutch", "Polish"]

    /// A language entered by hand in the configuration file stays selectable.
    private var choices: [String] {
        Self.languages.contains(config.summaryLanguage) ? Self.languages : Self.languages + [config.summaryLanguage]
    }

    var body: some View {
        SettingsCard {
            SettingRow("text.bubble", "Summary language",
                       "The language Tidywise writes summaries and keywords in. Pick the one you search in.") {
                Picker("Summary language", selection: $config.summaryLanguage) {
                    ForEach(choices, id: \.self) { Text($0).tag($0) }
                }
                .labelsHidden()
                .fixedSize()
            }
        }
    }
}

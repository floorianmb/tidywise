import SwiftUI

/// Home page: first the answer to “Do I need to do anything?”, then what's there.
struct HomeView: View {
    @Environment(AppState.self) private var state
    @AppStorage("introDismissed") private var introDismissed = false

    var body: some View {
        PageScroll(greeting, subtitle: "Tidywise keeps \(state.config.watchNames) tidy for you.", trailing: {
            if introDismissed {
                Button("How it works") { withAnimation(.snappy) { introDismissed = false } }
                    .buttonStyle(.link)
            }
        }, content: {
            HeroCard()
            notices
            if !introDismissed {
                IntroCard(interval: state.intervalText, folders: state.config.watchNames) { withAnimation(.snappy) { introDismissed = true } }
            }
            stats
            TopicsSection()
            RecentSection()
        })
    }

    private var greeting: String {
        switch Calendar.current.component(.hour, from: .now) {
        case 5..<11: "Good morning"
        case 11..<18: "Hello"
        default: "Good evening"
        }
    }

    /// What isn't already shown large in the hero card but still matters.
    @ViewBuilder private var notices: some View {
        if state.ready, !state.service.loaded, state.health != .noService {
            Notice(text: "Automation is not set up – Tidywise only sorts when you start it yourself.",
                   actionTitle: "Set up") { state.installService() }
        }
        if state.foldersBlocked {
            Notice(text: "macOS doesn't let Tidywise reach your folders in the background. That's why automation finds no files.",
                   systemImage: "lock.fill", actionTitle: "Allow access") { state.openSettings(.general) }
        } else if state.chromeBlocked {
            Notice(text: "Tidywise isn't allowed to read your Chrome history. That's why no new links are added.",
                   systemImage: "lock.fill", actionTitle: "Allow access") { state.openSettings(.general) }
        }
    }

    private var stats: some View {
        HStack(spacing: DS.gap) {
            StatButton(value: state.moved.count.formatted(Format.integer), label: "files sorted", symbol: "folder.fill", color: .blue) {
                state.showFiles(topic: nil)
            }
            StatButton(value: state.movedThisWeek.formatted(Format.integer), label: "of those in the last 7 days", symbol: "calendar", color: .teal) {
                state.showFiles(topic: nil)
            }
            if state.config.linksEnabled || !state.links.isEmpty {
                StatButton(value: state.links.count.formatted(Format.integer), label: "links saved", symbol: "link", color: .purple) {
                    state.showLinks(topic: nil)
                }
            } else {
                StatButton(value: state.config.topics.count.formatted(Format.integer), label: "topics", symbol: "square.grid.2x2.fill", color: .purple) {
                    state.openSettings(.topics)
                }
            }
        }
    }
}

// MARK: - Hero card

/// One state, one sentence, one action.
struct HeroCard: View {
    @Environment(AppState.self) private var state

    var body: some View {
        let health = state.health
        HStack(alignment: .center, spacing: 18) {
            icon(health)
            VStack(alignment: .leading, spacing: 5) {
                Text(title(health)).font(DS.heroFont)
                Text(subtitle(health))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .truncationMode(.middle)
                if health == .working {
                    progress.padding(.top, 6)
                } else if health == .noModel, let d = state.download, !d.failed {
                    VStack(alignment: .leading, spacing: 6) {
                        ProgressBar(value: d.fraction, tint: state.healthColor).frame(maxWidth: 420)
                        Text(d.detail).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    .padding(.top, 6)
                }
            }
            Spacer(minLength: 16)
            action(health)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(padding: 22, radius: 20, tint: health == .starting ? nil : state.healthColor)
        .animation(.snappy, value: health)
    }

    @ViewBuilder private func icon(_ health: Health) -> some View {
        switch health {
        case .starting:
            ProgressView().controlSize(.regular).frame(width: 56, height: 56)
        case .setup:
            IconBadge(symbol: "sparkles", color: state.healthColor, size: 56, round: true)
        case .noModel:
            IconBadge(symbol: "arrow.down.circle.fill", color: state.healthColor, size: 56, round: true)
        case .working:
            IconBadge(symbol: "sparkles", color: state.healthColor, size: 56, round: true)
                .symbolEffect(.pulse, isActive: true)
        case .paused:
            IconBadge(symbol: "moon.zzz.fill", color: state.healthColor, size: 56, round: true)
        case .offline:
            IconBadge(symbol: "bolt.slash.fill", color: state.healthColor, size: 56, round: true)
        case .needsReview:
            IconBadge(symbol: "tray.full.fill", color: state.healthColor, size: 56, round: true)
        case .noService:
            IconBadge(symbol: "clock.badge.exclamationmark.fill", color: state.healthColor, size: 56, round: true)
        case .ok:
            IconBadge(symbol: "checkmark", color: state.healthColor, size: 56, round: true)
        }
    }

    private func title(_ health: Health) -> String {
        switch health {
        case .starting: "One moment …"
        case .setup: "Tidywise isn't set up yet"
        case .paused: "AI is turned off"
        case .offline: "AI is not reachable"
        case .noModel: state.download?.failed == false ? "The AI model is downloading" : "The AI model is still missing"
        case .working: state.activity?.isAlive == true ? (state.activity?.task ?? "Tidywise is sorting") : (state.runningCommand ?? "Tidywise is working")
        case .needsReview: state.reviewCount == 1 ? "1 file is waiting for you" : "\(state.reviewCount) files are waiting for you"
        case .noService: "Automation is not set up"
        case .ok: "All tidy"
        }
    }

    private func subtitle(_ health: Health) -> String {
        switch health {
        case .starting:
            return "Tidywise is checking how things stand."
        case .paused:
            if state.stopping { return "Stopping running work and freeing memory …" }
            if state.reviewCount > 0 { return "It uses no memory. You can still review and move files." }
            return "It uses no memory. Tidywise won't sort anything until you turn it back on."
        case .setup:
            return "A few steps are still missing."
        case .offline:
            if let busy = state.ollamaBusy { return busy }
            if !state.config.usesOllama {
                return "The model server at \(state.config.serverURL) doesn't answer. Start it in LM Studio, or run mlx_lm.server or llama-server."
            }
            return OllamaSetup.isInstalled
                ? "Ollama isn't running. Without this local AI program, Tidywise can't sort anything."
                : "Ollama isn't installed. This program runs the AI on your Mac."
        case .noModel:
            if !state.config.usesOllama {
                return "The model server doesn't offer \(state.config.model). Pick one of its models in Settings."
            }
            if let d = state.download, d.failed { return "The download didn't work: \(d.detail)" }
            if state.download != nil { return "\(state.config.model) – just this once, after that everything runs without internet." }
            return "\(state.config.model) needs to be downloaded once. After that everything runs without internet."
        case .working:
            if let a = state.activity, a.isAlive, let label = a.label, !label.isEmpty { return label }
            return "This will take a moment."
        case .needsReview:
            if state.planned.isEmpty { return "Tidywise wasn't sure about these files. Just choose the right topic." }
            return "Tidywise has suggested where they belong. A quick look is enough."
        case .noService:
            return "Without it, Tidywise only sorts when you click “Check for new files” at the top. One click sets it up."
        case .ok:
            return "Last checked \(Format.relative(state.lastRun)) · next check \(state.nextRunText)"
        }
    }

    private var progress: some View {
        let a = state.activity?.isAlive == true ? state.activity : nil
        return VStack(alignment: .leading, spacing: 6) {
            ProgressBar(value: a?.progress, tint: state.healthColor).frame(maxWidth: 420)
            if let done = a?.done, let total = a?.total {
                Text("\(done.formatted(Format.integer)) of \(total.formatted(Format.integer)) done")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private func action(_ health: Health) -> some View {
        Group {
            switch health {
            case .starting, .working, .setup:
                EmptyView()
            case .noModel:
                if !state.config.usesOllama {
                    Button("Model settings") { state.openSettings(.model) }.buttonStyle(.borderedProminent)
                } else if state.download?.failed == false {
                    Button("Cancel") { state.cancelDownload() }
                } else {
                    Button { state.downloadModel(state.config.model, select: false) } label: {
                        Label(ModelCatalog.option(state.config.model).map { "Download model · \(Format.fileSize($0.downloadBytes))" } ?? "Download model",
                              systemImage: "arrow.down.circle")
                    }
                    .buttonStyle(.borderedProminent)
                }
            case .paused:
                Button { state.setAIEnabled(true) } label: { Label("Turn AI on", systemImage: "power") }
                    .buttonStyle(.borderedProminent)
                    .disabled(state.stopping)
            case .offline:
                if !state.config.usesOllama {
                    Button("Model settings") { state.openSettings(.model) }.buttonStyle(.borderedProminent)
                } else if OllamaSetup.isInstalled {
                    Button { state.startOllama() } label: { Label("Start Ollama", systemImage: "play.fill") }
                        .buttonStyle(.borderedProminent)
                        .disabled(state.ollamaBusy != nil)
                } else {
                    Button("Set up Ollama") { state.openSettings(.model) }.buttonStyle(.borderedProminent)
                }
            case .needsReview:
                Button { state.page = .review } label: { Label("Review now", systemImage: "arrow.right") }
                    .buttonStyle(.borderedProminent)
            case .noService:
                Button("Set up automation") { state.installService() }
                    .buttonStyle(.borderedProminent)
                    .disabled(state.runningCommand != nil)
            case .ok:
                Button { state.scan() } label: { Label("Check for new files", systemImage: "arrow.clockwise") }
                    .disabled(!state.canRunAI)
            }
        }
        .controlSize(.large)
    }
}

// MARK: - Home page building blocks

struct StatButton: View {
    let value: String
    let label: String
    let symbol: String
    let color: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                IconBadge(symbol: symbol, color: color, size: 40)
                VStack(alignment: .leading, spacing: 1) {
                    Text(value).font(DS.numberFont).contentTransition(.numericText())
                    Text(label).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
            }
        }
        .buttonStyle(CardButtonStyle())
    }
}

/// Explains in three steps on first launch what the app does. Can be dismissed and shown again.
struct IntroCard: View {
    let interval: String
    let folders: String
    let dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("How Tidywise works").font(DS.sectionFont)
                Spacer()
                Button("Got it", action: dismiss).controlSize(.small)
            }
            HStack(alignment: .top, spacing: 20) {
                step(1, "eye.fill", .blue, "Tidywise takes a look",
                     "\(interval.prefix(1).uppercased() + interval.dropFirst()), while the app is open – in \(folders), loose files only. Folders stay as they are.")
                step(2, "sparkles", DS.accent, "The AI reads along",
                     "An AI model on your Mac works out what a file is about and suggests a topic. Nothing leaves your computer.")
                step(3, "checkmark", .green, "You decide",
                     "Under “Review” you confirm the suggestions. Everything can be changed or put back later.")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func step(_ number: Int, _ symbol: String, _ color: Color, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            IconBadge(symbol: symbol, color: color, size: 34)
            VStack(alignment: .leading, spacing: 3) {
                Text("\(number). \(title)").font(.body.weight(.semibold))
                Text(text).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }
}

struct TopicsSection: View {
    @Environment(AppState.self) private var state

    private struct Entry: Identifiable {
        let topic: Topic
        let files: Int
        let links: Int
        var id: String { topic.slug }
    }

    var body: some View {
        let fileCounts = Dictionary(grouping: state.moved.filter { !$0.isInInbox }, by: { $0.topic ?? "" }).mapValues(\.count)
        let linkCounts = Dictionary(grouping: state.links, by: { $0.topic ?? "" }).mapValues(\.count)
        let known = Set(state.config.topics.map(\.slug))
        let orphanSlugs = Set(fileCounts.keys).union(linkCounts.keys).subtracting(known).filter { !$0.isEmpty }.sorted()
        let allTopics = state.config.topics + orphanSlugs.map { Topic(slug: $0, name: $0) }
        let entries = allTopics
            .map { Entry(topic: $0, files: fileCounts[$0.slug] ?? 0, links: linkCounts[$0.slug] ?? 0) }
            .filter { $0.files + $0.links > 0 }
            .sorted { ($0.files, $0.links) > ($1.files, $1.links) }

        if !entries.isEmpty {
            SectionHeader("Your topics", detail: "\(entries.count) with content")
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 230), spacing: 12)], spacing: 12) {
                ForEach(entries) { e in
                    Button {
                        if e.files > 0 { state.showFiles(topic: e.topic.slug) } else { state.showLinks(topic: e.topic.slug) }
                    } label: {
                        let style = TopicStyle.of(e.topic.slug)
                        HStack(spacing: 12) {
                            IconBadge(symbol: style.symbol, color: style.color, size: 38)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(e.topic.name).font(.body.weight(.semibold)).lineLimit(1)
                                Text(caption(e.files, e.links)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer(minLength: 0)
                        }
                    }
                    .buttonStyle(CardButtonStyle(padding: 14))
                    .help(e.files > 0 ? "Show files for “\(e.topic.name)”" : "Show links for “\(e.topic.name)”")
                }
            }
        }
    }

    private func caption(_ files: Int, _ links: Int) -> String {
        var parts: [String] = []
        if files > 0 { parts.append(files == 1 ? "1 file" : "\(files.formatted(Format.integer)) files") }
        if links > 0 { parts.append(links == 1 ? "1 link" : "\(links.formatted(Format.integer)) links") }
        return parts.joined(separator: " · ")
    }
}

struct RecentSection: View {
    @Environment(AppState.self) private var state

    var body: some View {
        let recent = state.moved.sorted { ($0.movedAt ?? .distantPast) > ($1.movedAt ?? .distantPast) }.prefix(5)
        if !recent.isEmpty {
            SectionHeader("Recently sorted") {
                Button("All files") { state.showFiles(topic: nil) }.buttonStyle(.link)
            }
            VStack(spacing: 2) {
                ForEach(Array(recent)) { f in
                    Button { state.showFile(f) } label: {
                        HStack(spacing: 12) {
                            FileIconView(ext: f.ext, size: 30)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(f.name).lineLimit(1).truncationMode(.middle)
                                Text(f.summary).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer(minLength: 12)
                            TopicPill(slug: f.isInInbox ? "_inbox" : f.topic,
                                      name: f.isInInbox ? "Inbox" : state.config.topicName(f.topic))
                            Text(Format.shortRelative(f.movedAt))
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                                .frame(width: 84, alignment: .trailing)
                        }
                        .hoverHighlight()
                    }
                    .buttonStyle(.plain)
                }
            }
            .frame(maxWidth: .infinity)
            .card(padding: 8)
        }
    }
}

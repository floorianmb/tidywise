import AppKit
import Charts
import SwiftUI

/// Everything that runs in the background: automation and AI model. Technical details are collapsed.
/// How Tidywise should behave is set under “Settings”.
struct SystemView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        PageScroll("System", subtitle: "What works for you in the background – and how it's doing.", trailing: {
            Button { state.page = .settings } label: { Label("Settings", systemImage: "gearshape") }
        }, content: {
            if state.accessBlocked { AccessCard() }

            HStack(alignment: .top, spacing: DS.gap) {
                automationCard
                modelCard
            }
            .fixedSize(horizontal: false, vertical: true)

            SectionHeader("For the curious")
            speedCard
            Collapsible("Recent AI calls", detail: callsDetail, systemImage: "list.bullet.rectangle") { callsTable }
            Collapsible("Log", detail: "What automation noted on each run", systemImage: "text.alignleft") { log }
        })
    }

    // MARK: Automation

    private var automationCard: some View {
        let loaded = state.service.loaded
        let busy = state.isWorking
        let on = state.aiEnabled
        let color: Color = !loaded ? DS.attention : (!on ? .gray : (busy ? DS.accent : DS.ok))
        return VStack(alignment: .leading, spacing: 14) {
            statusHeader(symbol: "clock.arrow.circlepath", color: color, kicker: "Automation",
                         title: !loaded ? "Not set up" : (!on ? "Paused" : (busy ? "Working right now" : "Checks \(state.intervalText)")),
                         pulsing: busy)
            Text(!loaded ? "Without automation, Tidywise only sorts when you click “Check for new files”."
                 : (on ? "Only runs while Tidywise is open – even if only the icon in the menu bar is there."
                       : "AI is off. Turn it back on at the top right or in the menu bar."))
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 8) {
                InfoRow("Last checked", Format.relative(state.lastRun))
                InfoRow("Next check", loaded ? state.nextRunText : "–")
                if state.config.linksEnabled {
                    InfoRow("Chrome history read", Format.relative(Format.date(state.meta["chrome_last_run"])))
                }
                InfoRow("Last run", state.exitCodeText)
            }
            .font(.callout)
            Spacer(minLength: 0)
            if loaded {
                Button { state.runServiceNow() } label: { Label("Run now", systemImage: "play.fill") }
                    .disabled(!state.canScan)
                    .help("Start a full run")
            } else {
                Button { state.installService() } label: { Label("Set up automation", systemImage: "plus.circle") }
                    .buttonStyle(.borderedProminent)
                    .disabled(state.runningCommand != nil)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .card()
    }

    // MARK: AI model

    private var modelCard: some View {
        let loaded = state.modelLoaded
        let busy = state.isWorking
        let on = state.aiEnabled
        let missing = state.ollamaUp && !state.modelInstalled
        let color: Color = !on ? .gray : (!state.ollamaUp ? DS.problem : (missing ? DS.attention : (loaded == nil ? .gray : (busy ? DS.accent : DS.ok))))
        return VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                statusHeader(symbol: "cpu.fill", color: color, kicker: "AI model · \(state.config.model)",
                             title: state.stopping ? "Turning off …" : (!on ? "Turned off" : (!state.ollamaUp ? "\(state.serverName) isn't running" : (missing ? (state.config.usesOllama ? "Not downloaded yet" : "Not offered by the server") : !state.config.usesOllama ? (busy ? "Computing right now" : "Ready") : loaded == nil ? "Asleep" : (busy ? "Computing right now" : "Loaded and ready")))),
                             pulsing: busy)
                Spacer(minLength: 8)
                AIToggle().controlSize(.regular)
            }
            Group {
                if !on {
                    Text("Uses no memory and doesn't start on its own. Reviewing, moving and searching still work.")
                } else if !state.ollamaUp, !state.config.usesOllama {
                    Text("Tidywise uses the local server at \(state.config.serverURL). Start it in LM Studio, or run mlx_lm.server or llama-server.")
                } else if !state.config.usesOllama {
                    Text(missing ? "Pick one of the server's models in Settings." : "Runs on your own server at \(state.config.serverURL), which manages its memory itself.")
                } else if !state.ollamaUp {
                    Text(OllamaSetup.isInstalled ? "Without Ollama, Tidywise can't sort anything. One click starts it."
                                                 : "Ollama runs the AI on your Mac and isn't installed yet.")
                } else if missing {
                    Text(state.download.map { $0.failed ? "The download didn't work." : "Downloading: \($0.detail)" }
                         ?? "The model needs to be downloaded once. After that everything runs without internet.")
                } else if let exp = loaded?.expiresAt, !busy {
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        Text("Uses \(Format.bytes(state.modelRAM)) of memory. Unloads on its own \(Format.relative(exp)).")
                    }
                } else if loaded != nil {
                    Text("Uses \(Format.bytes(state.modelRAM)) of memory.")
                } else {
                    Text("Uses no memory. Loads on its own as soon as there's something to do.")
                }
            }
            .font(.callout)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            MemoryBar(used: state.memoryUsed, model: state.modelRAM, total: state.memoryTotal)
            Spacer(minLength: 0)
            if !on {
                EmptyView()
            } else if !state.config.usesOllama {
                if !state.ollamaUp || missing { Button("Model settings") { state.openSettings(.model) } }
            } else if !state.ollamaUp {
                if OllamaSetup.isInstalled {
                    Button { state.startOllama() } label: { Label("Start Ollama", systemImage: "play.fill") }
                        .disabled(state.ollamaBusy != nil)
                } else {
                    Button("Set up Ollama") { state.openSettings(.model) }
                }
            } else if missing {
                Button("Choose and download a model") { state.openSettings(.model) }
            } else if loaded != nil {
                Button { state.unloadModel() } label: { Label("Free memory", systemImage: "eject.fill") }
                    .disabled(busy)
                    .help("Remove the model from memory right away")
            } else {
                Button { state.loadModel() } label: { Label("Load now", systemImage: "arrow.down.circle") }
                    .disabled(state.runningCommand != nil)
                    .help("Load the model ahead of time so the next run starts faster")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .card()
    }

    private func statusHeader(symbol: String, color: Color, kicker: String, title: String, pulsing: Bool) -> some View {
        HStack(spacing: 12) {
            IconBadge(symbol: symbol, color: color, size: 40)
            VStack(alignment: .leading, spacing: 2) {
                Text(kicker).font(.caption.weight(.medium)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                HStack(spacing: 8) {
                    Text(title).font(.system(size: 17, weight: .semibold, design: .rounded)).lineLimit(1)
                    if pulsing { StatusDot(color: color, pulsing: true) }
                }
            }
        }
    }

    // MARK: Speed

    private var speedCard: some View {
        let points = state.calls.prefix(80).reversed().compactMap { c -> (LLMCall, Double)? in
            c.tokensPerSecond.map { (c, $0) }
        }
        let ok = state.calls.prefix(30).filter(\.ok)
        let promptTPS = ok.compactMap(\.promptTokensPerSecond)
        let durations = ok.map(\.totalMs)
        let today = state.calls.filter { Calendar.current.isDateInToday($0.at) }
        let errors = state.calls.filter { !$0.ok }.count

        return VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    CardTitle(title: "AI speed", systemImage: "speedometer")
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(state.averageTokensPerSecond.map { Format.number($0) } ?? "–")
                            .font(.system(size: 34, weight: .semibold, design: .rounded).monospacedDigit())
                            .contentTransition(.numericText())
                        Text("tokens per second").font(.callout).foregroundStyle(.secondary)
                    }
                    Text("Average of the last \(min(30, state.calls.count)) calls. A token is roughly a syllable – more is faster.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }

            if points.count >= 2 {
                let avg = points.map(\.1).reduce(0, +) / Double(points.count)
                Chart {
                    ForEach(points, id: \.0.id) { c, tps in
                        AreaMark(x: .value("Time", c.at), y: .value("Tokens/s", tps))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(.linearGradient(colors: [DS.accent.opacity(0.22), .clear], startPoint: .top, endPoint: .bottom))
                        LineMark(x: .value("Time", c.at), y: .value("Tokens/s", tps))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(DS.accent)
                    }
                    RuleMark(y: .value("Avg", avg))
                        .foregroundStyle(Color.secondary.opacity(0.6))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                }
                .chartYScale(domain: .automatic(includesZero: false))
                .frame(height: 150)
            } else {
                Text("No measurements yet. As soon as Tidywise sorts something, the chart shows up here.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 80)
                    .well()
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 10)], alignment: .leading, spacing: 10) {
                fact("Time per call", durations.isEmpty ? "–" : Format.duration(ms: durations.reduce(0, +) / Double(durations.count)))
                fact("Reading input", promptTPS.isEmpty ? "–" : "\(Format.number(promptTPS.reduce(0, +) / Double(promptTPS.count), digits: 0)) tokens/s")
                fact("Today", "\(today.count) calls")
                fact("Errors", errors == 0 ? "none" : "\(errors) of \(state.calls.count)")
                if state.config.usesOllama {
                    fact("Ollama", state.ollamaUp ? (state.ollamaVersion.map { "Version \($0)" } ?? "running") : "off")
                } else {
                    fact("Model server", state.ollamaUp ? "running" : "off")
                }
                if let m = state.modelLoaded {
                    if let vram = m.size_vram, m.size > 0 { fact("On the GPU", "\(Int(Double(vram) / Double(m.size) * 100))%") }
                    if let ctx = m.context_length { fact("Context window", "\(ctx.formatted(Format.integer)) tokens") }
                }
                ForEach(state.processes) { p in
                    fact(p.displayName, "\(Format.bytes(p.rssBytes)) · \(Format.number(p.cpu, digits: 0))% CPU")
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func fact(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            Text(value).font(.callout.weight(.medium)).monospacedDigit().lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .well()
    }

    // MARK: Calls & log

    private var callsDetail: String {
        guard let last = state.calls.first else { return "No calls yet" }
        return "\(state.calls.count) calls · last one \(Format.relative(last.at))"
    }

    private var callsTable: some View {
        Table(Array(state.calls.prefix(300))) {
            TableColumn("Time") { c in Text(c.at.formatted(Date.FormatStyle(date: .omitted, time: .standard).locale(Format.locale))).monospacedDigit().foregroundStyle(.secondary) }
                .width(70)
            TableColumn("Type") { c in
                Label(c.kindLabel, systemImage: c.kind == "links" ? "link" : "doc")
            }.width(70)
            TableColumn("What") { c in
                HStack(spacing: 4) {
                    if c.images > 0 { Image(systemName: "photo").foregroundStyle(.secondary) }
                    if !c.ok { Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(DS.attention) }
                    Text(c.ok ? c.label : "\(c.label) – \(c.error ?? "Error")").lineLimit(1).truncationMode(.middle)
                }
            }
            TableColumn("Read") { c in Text(c.promptTokens.formatted(Format.integer)).monospacedDigit().frame(maxWidth: .infinity, alignment: .trailing) }.width(60)
            TableColumn("Written") { c in Text(c.outputTokens.formatted(Format.integer)).monospacedDigit().frame(maxWidth: .infinity, alignment: .trailing) }.width(80)
            TableColumn("Tokens/s") { c in
                Text(c.tokensPerSecond.map { Format.number($0) } ?? "–").monospacedDigit().frame(maxWidth: .infinity, alignment: .trailing)
            }.width(60)
            TableColumn("Duration") { c in Text(Format.duration(ms: c.totalMs)).monospacedDigit().frame(maxWidth: .infinity, alignment: .trailing) }.width(60)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .frame(height: 320)
        .clipShape(RoundedRectangle(cornerRadius: DS.innerRadius, style: .continuous))
    }

    private var log: some View {
        let lines = state.logTail.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        return VStack(alignment: .leading, spacing: 10) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 1) {
                        if state.logTail.isEmpty {
                            Text("No entries yet.").foregroundStyle(.secondary)
                        }
                        ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                            Text(line.isEmpty ? " " : line)
                                .foregroundStyle(color(for: line))
                                .fontWeight(line.hasPrefix("---") ? .semibold : .regular)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        Color.clear.frame(height: 1).id("end")
                    }
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                }
                .frame(height: 260)
                .well()
                .onChange(of: state.logTail) { proxy.scrollTo("end", anchor: .bottom) }
                .onAppear { proxy.scrollTo("end", anchor: .bottom) }
            }
            Button("Show log file in Finder") { NSWorkspace.shared.activateFileViewerSelecting([Paths.log]) }
                .buttonStyle(.link)
        }
    }

    private func color(for line: String) -> Color {
        if line.hasPrefix("---") { return .secondary }
        if line.contains("Errno") || line.contains("Error") || line.contains("Fehler") || line.contains("!") { return DS.attention }
        return .primary
    }
}

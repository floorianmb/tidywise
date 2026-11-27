import SwiftUI

/// The small window in the menu bar: state, the obvious next step, two key figures.
struct MenuBarView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                IconBadge(symbol: symbol, color: state.healthColor, size: 36, round: true)
                VStack(alignment: .leading, spacing: 1) {
                    Text(state.healthTitle).font(.system(size: 15, weight: .semibold, design: .rounded))
                    Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 0)
            }

            if state.health == .working {
                let a = state.activity?.isAlive == true ? state.activity : nil
                VStack(alignment: .leading, spacing: 5) {
                    ProgressBar(value: a?.progress)
                    if let d = a?.done, let t = a?.total {
                        Text("\(d.formatted(Format.integer)) of \(t.formatted(Format.integer)) done")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if state.reviewCount > 0, state.health != .needsReview {
                Button { show(.review) } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "checklist").foregroundStyle(DS.attention)
                        Text(state.reviewCount == 1 ? "Review 1 file" : "Review \(state.reviewCount) files").font(.callout.weight(.medium))
                        Spacer()
                        Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .background(DS.attention.opacity(0.13), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }

            HStack(spacing: 8) {
                tile(state.moved.count.formatted(Format.integer), "files sorted")
                tile(state.links.count.formatted(Format.integer), "links saved")
            }

            VStack(alignment: .leading, spacing: 8) {
                Toggle(isOn: Binding(get: { state.aiEnabled }, set: { state.setAIEnabled($0) })) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("AI").font(.callout.weight(.semibold))
                        Text(aiDetail).font(.caption.monospacedDigit()).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                .toggleStyle(.switch)
                .disabled(state.stopping)
                .help(state.aiEnabled ? "Turning it off stops running work and frees memory right away"
                                      : "Turn it on and Tidywise sorts \(state.intervalText) again")
                MemoryBar(used: state.memoryUsed, model: state.modelRAM, total: state.memoryTotal, compact: true)
            }
            .well(padding: 10)

            Divider()

            HStack(spacing: 8) {
                Button { show(state.health == .needsReview ? .review : state.page) } label: {
                    Text(state.health == .needsReview ? "Review now" : "Open Tidywise").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                Button { state.scan() } label: { Image(systemName: "arrow.clockwise") }
                    .help("Check for new files")
                    .disabled(!state.canScan)
                Button { NSApp.terminate(nil) } label: { Image(systemName: "power") }
                    .help("Quit Tidywise – then nothing is sorted in the background either")
            }
            .controlSize(.large)
        }
        .padding(16)
        .frame(width: 320)
    }

    private var symbol: String {
        switch state.health {
        case .starting: "hourglass"
        case .setup: "sparkles"
        case .paused: "moon.zzz.fill"
        case .offline: "bolt.slash.fill"
        case .noModel: "arrow.down.circle.fill"
        case .working: "sparkles"
        case .needsReview: "tray.full.fill"
        case .noService: "clock.badge.exclamationmark.fill"
        case .ok: "checkmark"
        }
    }

    private var detail: String {
        switch state.health {
        case .starting: "Loading data"
        case .setup: "Open Tidywise to set it up"
        case .paused: state.stopping ? "Stopping …" : "Won't sort anything until you turn it on"
        case .offline: "\(state.serverName) isn't running"
        case .noModel where !state.config.usesOllama: "Pick a model in Settings"
        case .noModel: state.download.map { $0.failed ? "Download failed" : $0.detail } ?? "Open Tidywise to download it"
        case .working: state.activity?.isAlive == true ? (state.activity?.label ?? state.activity?.task ?? "") : (state.runningCommand ?? "")
        case .noService: "Only sorts when you ask"
        case .needsReview, .ok: "Last checked \(Format.relative(state.lastRun))"
        }
    }

    private var aiDetail: String {
        if state.stopping { return "Turning off …" }
        if !state.aiEnabled { return "Off · uses no memory" }
        var parts: [String] = [state.modelLoaded != nil ? "Uses \(Format.bytes(state.modelRAM))" : "Asleep until the next run"]
        if let tps = state.isWorking ? state.lastCall?.tokensPerSecond : state.averageTokensPerSecond {
            parts.append("\(Format.number(tps, digits: 0)) tokens/s")
        }
        return parts.joined(separator: " · ")
    }

    private func show(_ page: Page) {
        state.page = page
        MainWindow.show()
    }

    private func tile(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value).font(.system(size: 18, weight: .semibold, design: .rounded)).monospacedDigit()
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .well(padding: 10)
    }
}

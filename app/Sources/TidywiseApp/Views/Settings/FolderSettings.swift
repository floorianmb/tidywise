import AppKit
import SwiftUI

// Building blocks for “Where does Tidywise look?” and “Where does it sort to?”. They work on a configuration that is
// passed in – in Settings that is the real one (every change is saved immediately),
// in setup a draft.

private let standardFolders = ["Downloads", "Documents", "Desktop"].map { Paths.home.appending(path: $0).path }

private func folderSymbol(_ path: String) -> String {
    switch path {
    case standardFolders[0]: "arrow.down.circle.fill"
    case standardFolders[1]: "doc.fill"
    case standardFolders[2]: "menubar.dock.rectangle"
    default: "folder.fill"
    }
}

/// The folders where Tidywise looks for loose files.
struct WatchFoldersEditor: View {
    @Environment(AppState.self) private var state
    @Binding var config: TidywiseConfig

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            CardTitle(title: "Where Tidywise looks", systemImage: "magnifyingglass")

            VStack(spacing: 2) {
                ForEach(config.watch, id: \.self) { row($0) }
                if config.watch.isEmpty {
                    Text("No folder yet. Without a folder, Tidywise has nothing to do.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .well()
                }
            }

            HStack(spacing: 8) {
                Button { add() } label: { Label("Add folder …", systemImage: "plus") }
                ForEach(standardFolders.filter { !config.watch.contains($0) }, id: \.self) { path in
                    Button { config.watch.append(path) } label: { Label(Paths.folderName(ofFolder: path), systemImage: "plus") }
                        .buttonStyle(.borderless)
                }
            }

            Label("Tidywise only touches loose files directly in these folders. Subfolders, projects and apps always stay as they are.",
                  systemImage: "hand.raised.fill")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func row(_ path: String) -> some View {
        let missing = !FileManager.default.fileExists(atPath: path)
        return HStack(spacing: 12) {
            IconBadge(symbol: folderSymbol(path), color: missing ? .gray : .blue, size: 30)
            VStack(alignment: .leading, spacing: 1) {
                Text(Paths.folderName(ofFolder: path)).font(.body.weight(.medium)).lineLimit(1)
                Text(missing ? "This folder doesn't exist (anymore)" : Paths.short(path))
                    .font(.caption)
                    .foregroundStyle(missing ? AnyShapeStyle(DS.attention) : AnyShapeStyle(.secondary))
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 12)
            Button { config.watch.removeAll { $0 == path } } label: { Image(systemName: "minus.circle") }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                .help("Stop looking in “\(Paths.folderName(ofFolder: path))”")
        }
        .hoverHighlight()
    }

    private func add() {
        guard let path = state.chooseFolder(title: "Which folder should Tidywise look in for loose files?", startingAt: Paths.home.path) else { return }
        if path == config.rootPath {
            state.result = CommandResult(title: "Add folder", output: "That's the destination folder – Tidywise sorts into it instead of looking there.", failed: true)
        } else if !config.watch.contains(path) {
            config.watch.append(path)
        }
    }
}

/// The destination folder and how it is structured.
struct DestinationEditor: View {
    @Environment(AppState.self) private var state
    @Binding var config: TidywiseConfig

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            CardTitle(title: "Where Tidywise sorts to", systemImage: "tray.and.arrow.down")

            HStack(spacing: 12) {
                IconBadge(symbol: "folder.fill", color: DS.accent, size: 30)
                VStack(alignment: .leading, spacing: 1) {
                    Text(config.root.lastPathComponent).font(.body.weight(.medium)).lineLimit(1)
                    Text(Paths.short(config.rootPath)).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 12)
                if FileManager.default.fileExists(atPath: config.rootPath) {
                    Button("Show in Finder") { NSWorkspace.shared.open(config.root) }.buttonStyle(.borderless)
                }
                Button("Change …") { change() }
            }
            .padding(.horizontal, 10)

            Divider()

            HStack(spacing: 0) {
                SettingLabel(symbol: "calendar", title: "Subfolders by date",
                             text: subfolderText)
                Picker("Subfolders by date", selection: $config.subfolders) {
                    Text("None").tag("none")
                    Text("By year").tag("year")
                    Text("Year and month").tag("month")
                }
                .labelsHidden()
                .pickerStyle(.segmented)
                .fixedSize()
            }

            StructurePreview(config: config)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private var subfolderText: String {
        switch config.subfolders {
        case "year": "Every topic gets one folder per year – based on the file's date."
        case "month": "Every topic gets folders per year and month – based on the file's date."
        default: "All files of a topic sit directly in the topic folder."
        }
    }

    private func change() {
        guard let chosen = state.chooseFolder(title: "Where should Tidywise sort to? Ideally a separate, empty folder.",
                                              startingAt: config.root.deletingLastPathComponent().path) else { return }
        if config.watch.contains(chosen) {
            // Using a folder Tidywise looks in as the destination would make it sort its own files.
            config.rootPath = URL(fileURLWithPath: chosen).appending(path: "Tidywise").path
            state.result = CommandResult(title: "Destination folder",
                                         output: "Tidywise already looks in “\(Paths.folderName(ofFolder: chosen))” – so it creates a subfolder “Tidywise” inside it.",
                                         failed: false)
        } else {
            config.rootPath = chosen
        }
    }
}

/// Shows as a small tree what the destination folder will look like – adapts to the settings.
struct StructurePreview: View {
    let config: TidywiseConfig

    private struct Line: Identifiable {
        let id = UUID()
        let depth: Int
        let symbol: String
        let name: String
        var note = ""
        var tint: Color = .secondary
        var dimmed = false
    }

    private var lines: [Line] {
        let topics = config.topics.filter { !$0.isFallback }
        let year = Calendar.current.component(.year, from: .now)
        var out = [Line(depth: 0, symbol: "folder.fill", name: config.root.lastPathComponent, tint: DS.accent),
                   Line(depth: 1, symbol: "folder.fill", name: config.folderName("topics", default: "Topics"), note: "one folder per topic")]
        if let first = topics.first {
            out.append(Line(depth: 2, symbol: first.symbol, name: first.slug, tint: TopicColor.color(first.color)))
            var depth = 3
            if config.subfolders != "none" {
                out.append(Line(depth: depth, symbol: "folder", name: "\(year)"))
                depth += 1
            }
            if config.subfolders == "month" {
                out.append(Line(depth: depth, symbol: "folder", name: "\(year)-03"))
                depth += 1
            }
            out.append(Line(depth: depth, symbol: "doc", name: "Example.pdf", dimmed: true))
        }
        if topics.count > 1 {
            let second = topics[1]
            out.append(Line(depth: 2, symbol: second.symbol, name: second.slug, tint: TopicColor.color(second.color)))
        }
        if config.topics.count > 2 {
            out.append(Line(depth: 2, symbol: "ellipsis", name: "\(config.topics.count - 2) more topics", dimmed: true))
        }
        out.append(Line(depth: 1, symbol: "questionmark.folder.fill", name: "_inbox", note: "unsure cases wait here for you", tint: .orange))
        out.append(Line(depth: 1, symbol: "lock.fill", name: config.folderName("private", default: "_private"), note: "nobody reads this – neither Tidywise nor an AI assistant"))
        if config.linksEnabled {
            out.append(Line(depth: 1, symbol: "link", name: "Links", note: "link collection from your browser history"))
        }
        out.append(Line(depth: 1, symbol: "calendar", name: config.folderName("days", default: "Days"), note: "what arrived on which day"))
        out.append(Line(depth: 1, symbol: "doc.text", name: "AGENTS.md", note: "guide for AI assistants like Claude"))
        return out
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            ForEach(lines) { line in
                HStack(spacing: 7) {
                    Image(systemName: line.symbol)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(line.tint)
                        .frame(width: 16)
                    Text(line.name)
                        .font(.system(.callout, design: .monospaced))
                        .foregroundStyle(line.dimmed ? .secondary : .primary)
                    if !line.note.isEmpty {
                        Text(line.note).font(.caption).foregroundStyle(.secondary)
                    }
                }
                .padding(.leading, CGFloat(line.depth) * 20)
                .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .well(padding: 14)
        .animation(.snappy(duration: 0.2), value: config.subfolders)
    }
}

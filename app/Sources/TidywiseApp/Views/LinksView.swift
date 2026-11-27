import AppKit
import SwiftUI

/// Pages and searches from the Chrome history, filtered by topic.
struct LinksView: View {
    @Environment(AppState.self) private var state
    @ViewState private var mode = Mode.pages
    @ViewState private var search = ""
    @ViewState private var selection = Set<LinkItem.ID>()

    enum Mode: String, CaseIterable, Identifiable {
        case pages = "Pages"
        case searches = "Searches"
        var id: String { rawValue }
    }

    private func matches(_ l: LinkItem) -> Bool {
        search.isEmpty || l.title.localizedCaseInsensitiveContains(search) || l.domain.localizedCaseInsensitiveContains(search)
    }

    var body: some View {
        let matching = state.links.filter(matches)
        let filtered = state.linkTopic == nil ? matching : matching.filter { $0.topic == state.linkTopic }
        let counts = Dictionary(grouping: matching, by: { $0.topic ?? "" }).mapValues(\.count)

        VStack(alignment: .leading, spacing: 14) {
            PageHeader("Links", subtitle: "Pages from your \(state.config.browserNames) history, sorted by topic by Tidywise. Double-click to open a page.") {
                Picker("View", selection: $mode) {
                    ForEach(Mode.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                Button { state.syncLinks() } label: { Label("Read history", systemImage: "clock.arrow.circlepath") }
                    .help(state.aiEnabled ? "Check your browser history for new pages now" : "AI is off")
                    .disabled(!state.canScan || !state.config.linksEnabled)
            }
            .padding(.horizontal, DS.pagePadding)
            .padding(.top, DS.pagePadding)

            if mode == .pages {
                let known = Set(state.config.topics.map(\.slug))
                let extraTopics = Set(counts.keys).subtracting(known).filter { !$0.isEmpty }.sorted().map { Topic(slug: $0, name: $0) }
                FilterChips(chips: [FilterChip(id: nil, title: "All", count: matching.count)] + (state.config.topics + extraTopics).compactMap { t in
                    guard let n = counts[t.slug], n > 0 else { return nil }
                    return FilterChip(id: t.slug, title: t.name, count: n,
                                      symbol: TopicStyle.of(t.slug).symbol, color: TopicStyle.of(t.slug).color)
                }, selection: Binding(get: { state.linkTopic }, set: { state.linkTopic = $0 }))
            }

            Group {
                if mode == .pages { pages(filtered) } else { searches }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipShape(RoundedRectangle(cornerRadius: DS.radius - 4, style: .continuous))
            .card(padding: 4)
            .padding(.horizontal, DS.pagePadding)
            .padding(.bottom, DS.pagePadding)
        }
        .pageFill()
        .background(DS.page)
        .searchable(text: $search, prompt: mode == .pages ? "Title or address" : "Search term")
    }

    @ViewBuilder private func pages(_ links: [LinkItem]) -> some View {
        if state.links.isEmpty, !state.config.linksEnabled {
            EmptyState(symbol: "link", color: .gray, title: "The link collection is off",
                       text: "If you like, Tidywise collects the pages from your browser history by topic – Chrome, Safari, Firefox, Arc, Brave, Vivaldi or Edge. Everything stays on your Mac.",
                       actionTitle: "Turn on in Settings") { state.openSettings(.general) }
        } else if state.links.isEmpty {
            EmptyState(symbol: state.chromeBlocked ? "lock.fill" : "link", color: state.chromeBlocked ? DS.attention : .gray,
                       title: "No links yet",
                       text: state.chromeBlocked
                           ? "Tidywise isn't allowed to read your \(state.blockedBrowsers) history. Settings shows you how to allow access."
                           : "Your \(state.config.browserNames) history is read regularly as soon as the AI is ready.",
                       actionTitle: state.chromeBlocked ? "Allow access" : (state.canScan ? "Read now" : nil)) {
                if state.chromeBlocked { state.openSettings(.general) } else { state.syncLinks() }
            }
        } else if links.isEmpty {
            EmptyState(symbol: "magnifyingglass", color: .gray, title: "No results",
                       text: search.isEmpty ? "There are no links for this topic." : "Nothing found for “\(search)”.")
        } else {
            List(links, selection: $selection) { l in
                LinkRow(link: l, showTopic: state.linkTopic == nil, topicName: state.config.topicName(l.topic))
            }
            .listStyle(.inset)
            .scrollContentBackground(.hidden)
            .contextMenu(forSelectionType: LinkItem.ID.self) { ids in
                Button("Open in browser") { open(ids) }
                Button("Copy link") { state.copyToClipboard(ids.joined(separator: "\n")) }
            } primaryAction: { ids in
                open(ids)
            }
        }
    }

    @ViewBuilder private var searches: some View {
        let terms = state.searches.filter { search.isEmpty || $0.term.localizedCaseInsensitiveContains(search) }
        if terms.isEmpty {
            EmptyState(symbol: "magnifyingglass", color: .gray, title: "No searches",
                       text: "What you recently searched for in your browser shows up here.")
        } else {
            List(terms) { s in
                Button {
                    search = s.term
                    state.linkTopic = nil
                    mode = .pages
                } label: {
                    HStack(spacing: 11) {
                        Image(systemName: "magnifyingglass")
                            .foregroundStyle(.secondary)
                            .frame(width: 28, height: 28)
                            .background(DS.fill, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
                        Text(s.term).lineLimit(1)
                        Spacer(minLength: 8)
                        Text(Format.shortRelative(s.at)).font(.caption).foregroundStyle(.tertiary)
                    }
                    .padding(.vertical, 3)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Show pages matching “\(s.term)”")
            }
            .listStyle(.inset)
            .scrollContentBackground(.hidden)
        }
    }

    private func open(_ ids: Set<String>) {
        for id in ids { if let url = URL(string: id) { NSWorkspace.shared.open(url) } }
    }
}

struct LinkRow: View {
    let link: LinkItem
    let showTopic: Bool
    let topicName: String

    var body: some View {
        let color = Color.topic(link.topic)
        HStack(spacing: 11) {
            Text(String(link.domain.replacingOccurrences(of: "www.", with: "").prefix(1)).uppercased())
                .font(.system(size: 13, weight: .bold, design: .rounded))
                .foregroundStyle(color)
                .frame(width: 28, height: 28)
                .background(color.opacity(0.15), in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(link.title.isEmpty ? link.url : link.title).lineLimit(1)
                Text(link.visits > 1 ? "\(link.domain) · visited \(link.visits) times" : link.domain)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            if showTopic, link.topic != nil, link.topic != "other", link.topic != "sonstiges" {
                TopicPill(slug: link.topic, name: topicName)
            }
            Text(Format.shortRelative(link.lastVisit))
                .font(.caption)
                .foregroundStyle(.tertiary)
                .frame(width: 84, alignment: .trailing)
        }
        .padding(.vertical, 3)
        .help(link.url)
    }
}

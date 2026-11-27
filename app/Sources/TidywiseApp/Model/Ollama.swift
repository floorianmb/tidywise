import Foundation

struct Ollama {
    let baseURL: String

    private struct PS: Decodable { let models: [LoadedModel] }
    private struct Tags: Decodable { let models: [InstalledModel] }

    /// One line of the progress from `/api/pull`.
    struct PullEvent: Decodable {
        let status: String?
        let digest: String?
        let total: Int64?
        let completed: Int64?
        let error: String?
    }
    private struct Version: Decodable { let version: String }

    private func get<T: Decodable>(_ path: String, as: T.Type) async -> T? {
        guard let url = URL(string: baseURL + path) else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 2
        guard let (data, _) = try? await URLSession.shared.data(for: req) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    func version() async -> String? { await get("/api/version", as: Version.self)?.version }

    func loadedModels() async -> [LoadedModel]? { await get("/api/ps", as: PS.self)?.models }

    /// Models that are on disk. `nil` if Ollama doesn't respond.
    func installedModels() async -> [InstalledModel]? { await get("/api/tags", as: Tags.self)?.models }

    /// Downloads a model and reports the progress line by line. If the task is cancelled,
    /// the connection closes and Ollama pauses – a later download picks up from there.
    func pull(model: String) -> AsyncThrowingStream<PullEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    guard let url = URL(string: baseURL + "/api/pull") else { throw URLError(.badURL) }
                    var req = URLRequest(url: url)
                    req.httpMethod = "POST"
                    req.timeoutInterval = 300  // how long there may be silence between two messages
                    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    req.httpBody = try JSONSerialization.data(withJSONObject: ["model": model, "stream": true])
                    let (bytes, _) = try await URLSession.shared.bytes(for: req)
                    for try await line in bytes.lines {
                        guard let data = line.data(using: .utf8),
                              let event = try? JSONDecoder().decode(PullEvent.self, from: data) else { continue }
                        continuation.yield(event)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// keepAlive "0" unloads the model right away, "5m" preloads it.
    func setKeepAlive(model: String, keepAlive: String) async {
        guard let url = URL(string: baseURL + "/api/generate") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 120
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["model": model, "keep_alive": keepAlive])
        _ = try? await URLSession.shared.data(for: req)
    }

    // MARK: Synchronous (for quitting the app)

    private static func requestSync(_ req: URLRequest, timeout: TimeInterval) -> Data? {
        var req = req
        req.timeoutInterval = timeout
        final class Box: @unchecked Sendable { var data: Data? }
        let box = Box()
        let done = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: req) { data, _, _ in
            box.data = data
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + timeout + 0.5)
        return box.data
    }

    static func loadedModelNamesSync(baseURL: String, timeout: TimeInterval) -> [String] {
        guard let url = URL(string: baseURL + "/api/ps"), let data = requestSync(URLRequest(url: url), timeout: timeout),
              let ps = try? JSONDecoder().decode(PS.self, from: data) else { return [] }
        return ps.models.map(\.name)
    }

    static func unloadSync(baseURL: String, model: String, timeout: TimeInterval) {
        guard let url = URL(string: baseURL + "/api/generate") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["model": model, "keep_alive": 0])
        _ = requestSync(req, timeout: timeout)
    }
}

/// Another local model server with an OpenAI-style API: LM Studio, mlx-lm (`mlx_lm.server`), llama.cpp (`llama-server`).
/// Tidywise only asks it which models it offers – loading and unloading is up to that server.
struct LocalServer {
    let baseURL: String

    private struct Models: Decodable {
        struct Model: Decodable { let id: String }
        let data: [Model]
    }

    /// The names of the models the server offers. `nil` if it doesn't respond.
    func modelIDs() async -> [String]? {
        guard let url = URL(string: baseURL + "/models") else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 2
        guard let (data, _) = try? await URLSession.shared.data(for: req) else { return nil }
        return (try? JSONDecoder().decode(Models.self, from: data))?.data.map(\.id)
    }
}

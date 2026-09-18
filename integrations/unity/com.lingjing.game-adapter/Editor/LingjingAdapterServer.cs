#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace Lingjing.GameAdapter.Editor
{
    internal static class LingjingAdapterServer
    {
        [Serializable]
        private sealed class CapabilitiesPayload
        {
            public string adapter_id;
            public string engine = "unity";
            public string engine_version;
            public string protocol_version = "1.0";
            public bool supports_dry_run = true;
            public bool supports_snapshot = true;
            public bool supports_logs = true;
            public bool supports_screenshots = true;
            public bool supports_video = false;
            public bool supports_audio = false;
            public bool mutating_actions = false;
        }

        [Serializable]
        private sealed class TicketPayload
        {
            public string ticket_id;
            public string adapter_id;
            public string action_id;
            public string scope_digest;
            public string request_digest;
            public double issued_at;
            public double expires_at;
            public string nonce;
            public string signature;
        }

        [Serializable]
        private sealed class ExecuteRequestPayload
        {
            public string action_id;
            public string[] evidence_requests;
            public bool dry_run;
            public TicketPayload ticket;
        }

        [Serializable]
        private sealed class EvidenceMetadata
        {
            public string mime;
            public string engine_object;
            public long byte_size;
            public int width;
            public int height;
            public string scene;
            public string play_mode;
        }

        [Serializable]
        private sealed class EvidencePayload
        {
            public string kind;
            public string locator;
            public string sha256;
            public EvidenceMetadata metadata;
        }

        [Serializable]
        private sealed class MetricsPayload
        {
            public bool loopback = true;
            public bool mutating_actions = false;
            public string bridge_mode = "editor-readonly-evidence";
            public int evidence_count;
        }

        [Serializable]
        private sealed class ExecuteResultPayload
        {
            public string adapter_id;
            public string action_id;
            public string ticket_id;
            public string status;
            public string before_snapshot_digest;
            public string after_snapshot_digest;
            public EvidencePayload[] evidence;
            public MetricsPayload metrics = new MetricsPayload();
            public string message;
        }

        private static readonly object Sync = new object();
        private static HttpListener _listener;
        private static CancellationTokenSource _cancellation;
        private static string _token = string.Empty;
        private static CapabilitiesPayload _capabilities;

        internal static bool IsRunning
        {
            get
            {
                lock (Sync)
                {
                    return _listener != null && _listener.IsListening;
                }
            }
        }

        internal static string Endpoint { get; private set; } = string.Empty;

        internal static string AdapterId => _capabilities != null ? _capabilities.adapter_id : string.Empty;

        internal static void Start(int port, string bearerToken)
        {
            if (port < 1024 || port > 65535)
            {
                throw new ArgumentOutOfRangeException(nameof(port), "Port must be between 1024 and 65535.");
            }

            Stop();
            CacheProjectIdentity();

            var listener = new HttpListener();
            var endpoint = $"http://127.0.0.1:{port}/";
            listener.Prefixes.Add(endpoint);
            listener.Start();

            lock (Sync)
            {
                _listener = listener;
                _cancellation = new CancellationTokenSource();
                _token = bearerToken ?? string.Empty;
                Endpoint = endpoint.TrimEnd('/');
            }

            _ = Task.Run(() => AcceptLoop(listener, _cancellation.Token));
        }

        internal static void Stop()
        {
            lock (Sync)
            {
                try
                {
                    _cancellation?.Cancel();
                    _listener?.Stop();
                    _listener?.Close();
                }
                catch (HttpListenerException)
                {
                    // Listener shutdown can race with an in-flight accept.
                }
                finally
                {
                    _listener = null;
                    _cancellation?.Dispose();
                    _cancellation = null;
                    _token = string.Empty;
                    Endpoint = string.Empty;
                }
            }
        }

        internal static string DescribeCapabilities()
        {
            return _capabilities == null ? "Bridge not started" : JsonUtility.ToJson(_capabilities, true);
        }

        private static void CacheProjectIdentity()
        {
            var projectPath = Directory.GetParent(Application.dataPath)?.FullName ?? Application.dataPath;
            var normalizedPath = projectPath.Replace('\\', '/');
            var identity = $"{normalizedPath}|{Application.unityVersion}|{Application.productName}";
            var hash = Sha256(identity);
            _capabilities = new CapabilitiesPayload
            {
                adapter_id = $"unity-{hash.Substring(0, 16)}",
                engine_version = Application.unityVersion,
            };
        }

        private static async Task AcceptLoop(HttpListener listener, CancellationToken cancellationToken)
        {
            while (!cancellationToken.IsCancellationRequested && listener.IsListening)
            {
                HttpListenerContext context;
                try
                {
                    context = await listener.GetContextAsync().ConfigureAwait(false);
                }
                catch (Exception) when (cancellationToken.IsCancellationRequested || !listener.IsListening)
                {
                    break;
                }
                catch (HttpListenerException)
                {
                    break;
                }

                _ = Task.Run(() => Handle(context), cancellationToken);
            }
        }

        private static void Handle(HttpListenerContext context)
        {
            try
            {
                if (!Authorized(context.Request))
                {
                    WriteJson(context.Response, 401, "{\"detail\":\"unauthorized\"}");
                    return;
                }

                var path = context.Request.Url?.AbsolutePath ?? string.Empty;
                if (context.Request.HttpMethod == "GET" && path == "/v1/adapter/capabilities")
                {
                    WriteJson(context.Response, 200, JsonUtility.ToJson(_capabilities));
                    return;
                }

                if (context.Request.HttpMethod == "POST" && path == "/v1/adapter/execute")
                {
                    HandleExecute(context);
                    return;
                }

                const string evidencePrefix = "/v1/adapter/evidence/";
                if (context.Request.HttpMethod == "GET" && path.StartsWith(evidencePrefix, StringComparison.Ordinal))
                {
                    var evidenceId = path.Substring(evidencePrefix.Length);
                    HandleEvidence(context, evidenceId);
                    return;
                }

                WriteJson(context.Response, 404, "{\"detail\":\"not found\"}");
            }
            catch (TimeoutException exception)
            {
                WriteJson(context.Response, 503, $"{{\"detail\":\"{EscapeJson(exception.Message)}\"}}");
            }
            catch (Exception exception)
            {
                var message = EscapeJson(exception.Message);
                WriteJson(context.Response, 500, $"{{\"detail\":\"{message}\"}}");
            }
        }

        private static bool Authorized(HttpListenerRequest request)
        {
            if (string.IsNullOrEmpty(_token))
            {
                return true;
            }

            var authorization = request.Headers["Authorization"] ?? string.Empty;
            return string.Equals(authorization, $"Bearer {_token}", StringComparison.Ordinal);
        }

        private static void HandleExecute(HttpListenerContext context)
        {
            string raw;
            using (var reader = new StreamReader(context.Request.InputStream, context.Request.ContentEncoding ?? Encoding.UTF8))
            {
                raw = reader.ReadToEnd();
            }

            var request = JsonUtility.FromJson<ExecuteRequestPayload>(raw);
            if (request == null || request.ticket == null || string.IsNullOrEmpty(request.action_id))
            {
                WriteJson(context.Response, 400, "{\"detail\":\"invalid execution request\"}");
                return;
            }

            if (!string.Equals(request.ticket.adapter_id, _capabilities.adapter_id, StringComparison.Ordinal)
                || !string.Equals(request.ticket.action_id, request.action_id, StringComparison.Ordinal))
            {
                WriteJson(context.Response, 409, "{\"detail\":\"ticket identity mismatch\"}");
                return;
            }

            var beforeDigest = LingjingEvidenceCache.SnapshotDigest;
            if (!request.dry_run)
            {
                var rejected = new ExecuteResultPayload
                {
                    adapter_id = _capabilities.adapter_id,
                    action_id = request.action_id,
                    ticket_id = request.ticket.ticket_id,
                    status = "rejected",
                    before_snapshot_digest = beforeDigest,
                    after_snapshot_digest = beforeDigest,
                    evidence = Array.Empty<EvidencePayload>(),
                    message = "Unity evidence package is read-only; mutating actions are disabled.",
                };
                WriteJson(context.Response, 200, JsonUtility.ToJson(rejected));
                return;
            }

            var captured = LingjingEvidenceCache.Capture(request.evidence_requests);
            var evidence = new List<EvidencePayload>();
            foreach (var record in captured)
            {
                evidence.Add(new EvidencePayload
                {
                    kind = record.kind,
                    locator = $"{Endpoint}/v1/adapter/evidence/{record.id}",
                    sha256 = record.sha256,
                    metadata = new EvidenceMetadata
                    {
                        mime = record.metadata?.mime ?? record.mime,
                        engine_object = record.metadata?.engine_object ?? "UnityEditor",
                        byte_size = record.metadata?.byte_size ?? record.bytes.LongLength,
                        width = record.metadata?.width ?? 0,
                        height = record.metadata?.height ?? 0,
                        scene = record.metadata?.scene ?? string.Empty,
                        play_mode = record.metadata?.play_mode ?? string.Empty,
                    },
                });
            }
            var afterDigest = LingjingEvidenceCache.SnapshotDigest;
            var metrics = new MetricsPayload { evidence_count = evidence.Count };
            var result = new ExecuteResultPayload
            {
                adapter_id = _capabilities.adapter_id,
                action_id = request.action_id,
                ticket_id = request.ticket.ticket_id,
                status = "dry-run",
                before_snapshot_digest = beforeDigest,
                after_snapshot_digest = afterDigest,
                evidence = evidence.ToArray(),
                metrics = metrics,
                message = "Unity Editor read-only evidence captured. These are external engine observations, not a verifier decision.",
            };
            WriteJson(context.Response, 200, JsonUtility.ToJson(result));
        }

        private static void HandleEvidence(HttpListenerContext context, string evidenceId)
        {
            if (string.IsNullOrEmpty(evidenceId) || !LingjingEvidenceCache.TryGet(evidenceId, out var record))
            {
                WriteJson(context.Response, 404, "{\"detail\":\"evidence expired or not found\"}");
                return;
            }
            WriteBytes(
                context.Response,
                200,
                record.bytes,
                record.mime,
                record.filename,
                record.sha256);
        }

        private static void WriteJson(HttpListenerResponse response, int statusCode, string json)
        {
            WriteBytes(
                response,
                statusCode,
                Encoding.UTF8.GetBytes(json ?? "{}"),
                "application/json; charset=utf-8",
                null,
                null);
        }

        private static void WriteBytes(
            HttpListenerResponse response,
            int statusCode,
            byte[] bytes,
            string contentType,
            string filename,
            string sha256)
        {
            if (response.OutputStream == null)
            {
                return;
            }
            var payload = bytes ?? Array.Empty<byte>();
            response.StatusCode = statusCode;
            response.ContentType = string.IsNullOrEmpty(contentType) ? "application/octet-stream" : contentType;
            response.ContentLength64 = payload.LongLength;
            response.Headers["Cache-Control"] = "no-store";
            response.Headers["X-Content-Type-Options"] = "nosniff";
            if (!string.IsNullOrEmpty(sha256))
            {
                response.Headers["X-Lingjing-Sha256"] = sha256;
            }
            if (!string.IsNullOrEmpty(filename))
            {
                response.Headers["Content-Disposition"] = $"inline; filename=\"{filename.Replace("\"", string.Empty)}\"";
            }
            try
            {
                response.OutputStream.Write(payload, 0, payload.Length);
            }
            finally
            {
                response.OutputStream.Close();
            }
        }

        private static string Sha256(string value)
        {
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty));
                var builder = new StringBuilder(digest.Length * 2);
                foreach (var item in digest)
                {
                    builder.Append(item.ToString("x2"));
                }
                return builder.ToString();
            }
        }

        private static string EscapeJson(string value)
        {
            return (value ?? string.Empty)
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\r", "\\r")
                .Replace("\n", "\\n");
        }
    }
}
#endif

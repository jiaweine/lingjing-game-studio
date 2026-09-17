#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEditor;
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
            public bool supports_screenshots = false;
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
            public bool dry_run;
            public TicketPayload ticket;
        }

        [Serializable]
        private sealed class EvidenceMetadata
        {
            public string mime = "text/plain";
            public string engine_object = "UnityEditor";
        }

        [Serializable]
        private sealed class EvidencePayload
        {
            public string kind = "log";
            public string locator;
            public string sha256;
            public EvidenceMetadata metadata = new EvidenceMetadata();
        }

        [Serializable]
        private sealed class MetricsPayload
        {
            public bool loopback = true;
            public bool mutating_actions = false;
            public string bridge_mode = "editor-dry-run";
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
        private static string _snapshotDigest;
        private static string _projectLocator;

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
            _snapshotDigest = Sha256($"{identity}|editor-dry-run-v1");
            _projectLocator = $"unity://project/{hash.Substring(0, 16)}/editor-state";
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

                WriteJson(context.Response, 404, "{\"detail\":\"not found\"}");
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

            if (!request.dry_run)
            {
                var rejected = new ExecuteResultPayload
                {
                    adapter_id = _capabilities.adapter_id,
                    action_id = request.action_id,
                    ticket_id = request.ticket.ticket_id,
                    status = "rejected",
                    before_snapshot_digest = _snapshotDigest,
                    after_snapshot_digest = _snapshotDigest,
                    evidence = Array.Empty<EvidencePayload>(),
                    message = "Unity activation package is dry-run only; mutating actions are disabled.",
                };
                WriteJson(context.Response, 200, JsonUtility.ToJson(rejected));
                return;
            }

            var evidenceText = $"Lingjing Unity bridge dry-run; adapter={_capabilities.adapter_id}; unity={_capabilities.engine_version}";
            var evidence = new EvidencePayload
            {
                locator = _projectLocator,
                sha256 = Sha256(evidenceText),
            };
            var result = new ExecuteResultPayload
            {
                adapter_id = _capabilities.adapter_id,
                action_id = request.action_id,
                ticket_id = request.ticket.ticket_id,
                status = "dry-run",
                before_snapshot_digest = _snapshotDigest,
                after_snapshot_digest = _snapshotDigest,
                evidence = new[] { evidence },
                message = "Unity Editor dry-run conformance completed. This is an external engine observation, not a verifier decision.",
            };
            WriteJson(context.Response, 200, JsonUtility.ToJson(result));
        }

        private static void WriteJson(HttpListenerResponse response, int statusCode, string json)
        {
            if (response.OutputStream == null)
            {
                return;
            }
            var bytes = Encoding.UTF8.GetBytes(json ?? "{}");
            response.StatusCode = statusCode;
            response.ContentType = "application/json; charset=utf-8";
            response.ContentEncoding = Encoding.UTF8;
            response.ContentLength64 = bytes.Length;
            response.Headers["Cache-Control"] = "no-store";
            try
            {
                response.OutputStream.Write(bytes, 0, bytes.Length);
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

#if UNITY_EDITOR
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Lingjing.GameAdapter.Editor
{
    [InitializeOnLoad]
    internal static class LingjingEvidenceCache
    {
        [Serializable]
        internal sealed class EvidenceMetadata
        {
            public string mime;
            public string engine_object;
            public long byte_size;
            public int width;
            public int height;
            public string scene;
            public string play_mode;
        }

        internal sealed class EvidenceRecord
        {
            public string id;
            public string kind;
            public string mime;
            public string filename;
            public string sha256;
            public byte[] bytes;
            public EvidenceMetadata metadata;
            public DateTime created_utc;
        }

        [Serializable]
        private sealed class SceneSnapshot
        {
            public string active_scene;
            public string scene_path;
            public int root_count;
            public bool is_loaded;
            public bool is_playing;
            public bool is_paused;
            public string product_name;
            public string unity_version;
        }

        private sealed class LogEntry
        {
            public string type;
            public string message;
            public DateTime created_utc;
        }

        private sealed class CaptureRequest
        {
            public readonly string[] Requested;
            public readonly ManualResetEventSlim Completed = new ManualResetEventSlim(false);
            public List<EvidenceRecord> Results;
            public Exception Error;

            public CaptureRequest(string[] requested)
            {
                Requested = requested ?? Array.Empty<string>();
            }
        }

        private const int MaxLogEntries = 120;
        private const int MaxEvidenceItems = 12;
        private const int MaxLogEvidenceChars = 64 * 1024;
        private static readonly TimeSpan EvidenceTtl = TimeSpan.FromMinutes(10);
        private static readonly object LogSync = new object();
        private static readonly object EvidenceSync = new object();
        private static readonly Queue<LogEntry> Logs = new Queue<LogEntry>();
        private static readonly Dictionary<string, EvidenceRecord> Evidence = new Dictionary<string, EvidenceRecord>(StringComparer.Ordinal);
        private static readonly Queue<string> EvidenceOrder = new Queue<string>();
        private static readonly ConcurrentQueue<CaptureRequest> Requests = new ConcurrentQueue<CaptureRequest>();
        private static readonly Regex AuthorizationValue = new Regex(
            @"(?i)[""']?authorization[""']?\s*[:=]\s*[""']?(?:[A-Za-z]+\s+)?[^""'\s,;]+",
            RegexOptions.Compiled);
        private static readonly Regex SensitiveValue = new Regex(
            @"(?i)[""']?(access[_-]?token|refresh[_-]?token|token|secret|password|api[_-]?key)[""']?\s*[:=]\s*[""']?[^""'\s,;]+",
            RegexOptions.Compiled);
        private static readonly int MainThreadId;
        private static string _snapshotJson = "{}";
        private static string _snapshotDigest = Sha256("{}");

        static LingjingEvidenceCache()
        {
            MainThreadId = Thread.CurrentThread.ManagedThreadId;
            Application.logMessageReceivedThreaded += OnLog;
            EditorApplication.update += OnEditorUpdate;
            RefreshSnapshot();
        }

        internal static string SnapshotDigest
        {
            get
            {
                lock (EvidenceSync)
                {
                    return _snapshotDigest;
                }
            }
        }

        internal static List<EvidenceRecord> Capture(string[] requested, int timeoutMilliseconds = 3500)
        {
            var normalized = NormalizeRequests(requested);
            if (Thread.CurrentThread.ManagedThreadId == MainThreadId)
            {
                return CaptureOnMainThread(normalized);
            }

            var request = new CaptureRequest(normalized);
            Requests.Enqueue(request);
            if (!request.Completed.Wait(Math.Max(500, timeoutMilliseconds)))
            {
                // Do not dispose here: the queued main-thread request can still complete later.
                throw new TimeoutException("Unity main-thread evidence capture timed out.");
            }
            try
            {
                if (request.Error != null)
                {
                    throw new InvalidOperationException("Unity evidence capture failed.", request.Error);
                }
                return request.Results ?? new List<EvidenceRecord>();
            }
            finally
            {
                request.Completed.Dispose();
            }
        }

        internal static bool TryGet(string id, out EvidenceRecord record)
        {
            CleanupExpired();
            lock (EvidenceSync)
            {
                return Evidence.TryGetValue(id ?? string.Empty, out record);
            }
        }

        private static string[] NormalizeRequests(string[] requested)
        {
            var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var item in requested ?? Array.Empty<string>())
            {
                var value = (item ?? string.Empty).Trim().ToLowerInvariant();
                if (value == "logs" || value == "log") set.Add("logs");
                else if (value == "snapshot" || value == "state") set.Add("snapshot");
                else if (value == "screenshot" || value == "screenshots") set.Add("screenshot");
            }
            if (set.Count == 0)
            {
                set.Add("logs");
                set.Add("snapshot");
            }
            var values = new string[set.Count];
            set.CopyTo(values);
            Array.Sort(values, StringComparer.Ordinal);
            return values;
        }

        private static void OnEditorUpdate()
        {
            RefreshSnapshot();
            var processed = 0;
            while (processed < 4 && Requests.TryDequeue(out var request))
            {
                try
                {
                    request.Results = CaptureOnMainThread(request.Requested);
                }
                catch (Exception exception)
                {
                    request.Error = exception;
                }
                finally
                {
                    request.Completed.Set();
                }
                processed += 1;
            }
            CleanupExpired();
        }

        private static List<EvidenceRecord> CaptureOnMainThread(string[] requested)
        {
            RefreshSnapshot();
            var results = new List<EvidenceRecord>();
            foreach (var item in requested)
            {
                EvidenceRecord record = null;
                if (item == "logs") record = CaptureLogs();
                else if (item == "snapshot") record = CaptureSnapshot();
                else if (item == "screenshot") record = CaptureScreenshot();
                if (record == null) continue;
                Store(record);
                results.Add(record);
            }
            RefreshSnapshot();
            return results;
        }

        private static EvidenceRecord CaptureLogs()
        {
            var builder = new StringBuilder();
            lock (LogSync)
            {
                foreach (var entry in Logs)
                {
                    var line = $"{entry.created_utc:O} [{entry.type}] {Redact(entry.message)}\n";
                    if (builder.Length + line.Length > MaxLogEvidenceChars) break;
                    builder.Append(line);
                }
            }
            if (builder.Length == 0)
            {
                builder.Append("No Unity logs captured since the Lingjing package initialized.\n");
            }
            var bytes = Encoding.UTF8.GetBytes(builder.ToString());
            return BuildRecord(
                "log",
                "text/plain; charset=utf-8",
                "unity-console.log",
                bytes,
                new EvidenceMetadata
                {
                    mime = "text/plain",
                    engine_object = "UnityEditor.Console",
                    byte_size = bytes.LongLength,
                    scene = SceneManager.GetActiveScene().name,
                    play_mode = EditorApplication.isPlaying ? "play" : "edit",
                });
        }

        private static EvidenceRecord CaptureSnapshot()
        {
            string json;
            lock (EvidenceSync)
            {
                json = _snapshotJson;
            }
            var bytes = Encoding.UTF8.GetBytes(json);
            return BuildRecord(
                "snapshot",
                "application/json; charset=utf-8",
                "unity-editor-snapshot.json",
                bytes,
                new EvidenceMetadata
                {
                    mime = "application/json",
                    engine_object = "UnityEditor.SceneState",
                    byte_size = bytes.LongLength,
                    scene = SceneManager.GetActiveScene().name,
                    play_mode = EditorApplication.isPlaying ? "play" : "edit",
                });
        }

        private static EvidenceRecord CaptureScreenshot()
        {
            var engineObject = string.Empty;
            var texture = CaptureCameraFrame(out engineObject);
            if (texture == null) return null;
            try
            {
                var bytes = texture.EncodeToPNG();
                return BuildRecord(
                    "screenshot",
                    "image/png",
                    "unity-camera-frame.png",
                    bytes,
                    new EvidenceMetadata
                    {
                        mime = "image/png",
                        engine_object = engineObject,
                        byte_size = bytes.LongLength,
                        width = texture.width,
                        height = texture.height,
                        scene = SceneManager.GetActiveScene().name,
                        play_mode = EditorApplication.isPlaying ? "play" : "edit",
                    });
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        private static Texture2D CaptureCameraFrame(out string engineObject)
        {
            Camera camera = null;
            engineObject = string.Empty;
            if (EditorApplication.isPlaying)
            {
                camera = Camera.main;
                if (camera != null) engineObject = "GameView/Camera.main";
            }
            if (camera == null && SceneView.lastActiveSceneView != null)
            {
                camera = SceneView.lastActiveSceneView.camera;
                if (camera != null) engineObject = "SceneView/Camera";
            }
            if (camera == null) return null;

            var sourceWidth = camera.pixelWidth > 0 ? camera.pixelWidth : 1280;
            var sourceHeight = camera.pixelHeight > 0 ? camera.pixelHeight : 720;
            var scale = Math.Min(1.0, Math.Min(1280.0 / sourceWidth, 720.0 / sourceHeight));
            var width = Math.Max(64, (int)Math.Round(sourceWidth * scale));
            var height = Math.Max(64, (int)Math.Round(sourceHeight * scale));
            var renderTexture = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32);
            var previousTarget = camera.targetTexture;
            var previousActive = RenderTexture.active;
            try
            {
                camera.targetTexture = renderTexture;
                camera.Render();
                RenderTexture.active = renderTexture;
                var texture = new Texture2D(width, height, TextureFormat.RGB24, false);
                texture.ReadPixels(new Rect(0, 0, width, height), 0, 0, false);
                texture.Apply(false, false);
                return texture;
            }
            finally
            {
                camera.targetTexture = previousTarget;
                RenderTexture.active = previousActive;
                RenderTexture.ReleaseTemporary(renderTexture);
            }
        }

        private static void RefreshSnapshot()
        {
            var scene = SceneManager.GetActiveScene();
            var snapshot = new SceneSnapshot
            {
                active_scene = scene.name ?? string.Empty,
                scene_path = scene.path ?? string.Empty,
                root_count = scene.IsValid() ? scene.rootCount : 0,
                is_loaded = scene.IsValid() && scene.isLoaded,
                is_playing = EditorApplication.isPlaying,
                is_paused = EditorApplication.isPaused,
                product_name = Application.productName ?? string.Empty,
                unity_version = Application.unityVersion ?? string.Empty,
            };
            var json = JsonUtility.ToJson(snapshot);
            var digest = Sha256(json);
            lock (EvidenceSync)
            {
                _snapshotJson = json;
                _snapshotDigest = digest;
            }
        }

        private static void OnLog(string condition, string stackTrace, LogType type)
        {
            var entry = new LogEntry
            {
                type = type.ToString(),
                message = Redact((condition ?? string.Empty).Replace("\r", " ").Replace("\n", " ")),
                created_utc = DateTime.UtcNow,
            };
            lock (LogSync)
            {
                Logs.Enqueue(entry);
                while (Logs.Count > MaxLogEntries) Logs.Dequeue();
            }
        }

        private static string Redact(string value)
        {
            var input = value ?? string.Empty;
            if (input.Length > 4000) input = input.Substring(0, 4000) + "…";
            input = AuthorizationValue.Replace(input, "authorization=[REDACTED]");
            return SensitiveValue.Replace(input, "$1=[REDACTED]");
        }

        private static EvidenceRecord BuildRecord(
            string kind,
            string mime,
            string filename,
            byte[] bytes,
            EvidenceMetadata metadata)
        {
            return new EvidenceRecord
            {
                id = Guid.NewGuid().ToString("N"),
                kind = kind,
                mime = mime,
                filename = filename,
                bytes = bytes ?? Array.Empty<byte>(),
                sha256 = Sha256(bytes ?? Array.Empty<byte>()),
                metadata = metadata,
                created_utc = DateTime.UtcNow,
            };
        }

        private static void Store(EvidenceRecord record)
        {
            lock (EvidenceSync)
            {
                Evidence[record.id] = record;
                EvidenceOrder.Enqueue(record.id);
                while (EvidenceOrder.Count > MaxEvidenceItems)
                {
                    var oldest = EvidenceOrder.Dequeue();
                    Evidence.Remove(oldest);
                }
            }
        }

        private static void CleanupExpired()
        {
            lock (EvidenceSync)
            {
                while (EvidenceOrder.Count > 0)
                {
                    var id = EvidenceOrder.Peek();
                    if (!Evidence.TryGetValue(id, out var record))
                    {
                        EvidenceOrder.Dequeue();
                        continue;
                    }
                    if (DateTime.UtcNow - record.created_utc <= EvidenceTtl) break;
                    EvidenceOrder.Dequeue();
                    Evidence.Remove(id);
                }
            }
        }

        private static string Sha256(string value)
        {
            return Sha256(Encoding.UTF8.GetBytes(value ?? string.Empty));
        }

        private static string Sha256(byte[] value)
        {
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(value ?? Array.Empty<byte>());
                var builder = new StringBuilder(digest.Length * 2);
                foreach (var item in digest) builder.Append(item.ToString("x2"));
                return builder.ToString();
            }
        }
    }
}
#endif

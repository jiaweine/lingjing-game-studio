#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using UnityEditor;
using UnityEngine;

namespace Lingjing.GameAdapter.Editor
{
    internal sealed class LingjingAdapterWindow : EditorWindow
    {
        private const string PortKey = "Lingjing.GameAdapter.Port";
        private const string TokenKey = "Lingjing.GameAdapter.Token";
        private const int DefaultPort = 9030;

        private int _port;
        private string _token;
        private string _testMessage = string.Empty;
        private MessageType _testType = MessageType.None;

        [MenuItem("Lingjing/Game Adapter Setup")]
        private static void Open()
        {
            var window = GetWindow<LingjingAdapterWindow>(true, "Lingjing Game Adapter", true);
            window.minSize = new Vector2(540f, 430f);
            window.Show();
        }

        private void OnEnable()
        {
            _port = EditorPrefs.GetInt(PortKey, DefaultPort);
            _token = EditorPrefs.GetString(TokenKey, string.Empty);
        }

        private void OnDisable()
        {
            EditorPrefs.SetInt(PortKey, _port);
            EditorPrefs.SetString(TokenKey, _token ?? string.Empty);
        }

        private void OnGUI()
        {
            EditorGUILayout.Space(8);
            EditorGUILayout.LabelField("Lingjing GameAdapter v1", EditorStyles.boldLabel);
            EditorGUILayout.HelpBox(
                "This package exposes an Editor-only loopback bridge for activation and read-only evidence capture. " +
                "It does not grant Lingjing canonical write authority and it does not turn observations into verified truth.",
                MessageType.Info
            );

            EditorGUILayout.Space(6);
            using (new EditorGUILayout.VerticalScope("box"))
            {
                EditorGUILayout.LabelField("Bridge", EditorStyles.boldLabel);
                _port = EditorGUILayout.IntField("Loopback port", _port);
                _token = EditorGUILayout.PasswordField("Bearer token (optional)", _token ?? string.Empty);

                using (new EditorGUILayout.HorizontalScope())
                {
                    if (GUILayout.Button("Generate token"))
                    {
                        _token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
                        GUI.FocusControl(null);
                    }
                    if (GUILayout.Button("Clear token"))
                    {
                        _token = string.Empty;
                        GUI.FocusControl(null);
                    }
                }

                EditorGUILayout.Space(4);
                if (!LingjingAdapterServer.IsRunning)
                {
                    if (GUILayout.Button("Start local bridge", GUILayout.Height(30)))
                    {
                        StartBridge();
                    }
                }
                else
                {
                    EditorGUILayout.LabelField("Status", "Running");
                    EditorGUILayout.SelectableLabel(LingjingAdapterServer.Endpoint, GUILayout.Height(18));
                    EditorGUILayout.LabelField("Adapter ID", LingjingAdapterServer.AdapterId);
                    using (new EditorGUILayout.HorizontalScope())
                    {
                        if (GUILayout.Button("Copy endpoint"))
                        {
                            EditorGUIUtility.systemCopyBuffer = LingjingAdapterServer.Endpoint;
                            ShowNotification(new GUIContent("Endpoint copied"));
                        }
                        if (GUILayout.Button("Test connection"))
                        {
                            TestConnection();
                        }
                        if (GUILayout.Button("Preview evidence"))
                        {
                            PreviewEvidence();
                        }
                        if (GUILayout.Button("Stop bridge"))
                        {
                            LingjingAdapterServer.Stop();
                            _testMessage = "Bridge stopped.";
                            _testType = MessageType.None;
                        }
                    }
                }
            }

            if (!string.IsNullOrEmpty(_testMessage))
            {
                EditorGUILayout.HelpBox(_testMessage, _testType);
            }

            EditorGUILayout.Space(6);
            using (new EditorGUILayout.VerticalScope("box"))
            {
                EditorGUILayout.LabelField("Connect Lingjing", EditorStyles.boldLabel);
                EditorGUILayout.LabelField("1. Start the local bridge.", EditorStyles.wordWrappedLabel);
                EditorGUILayout.LabelField("2. Preview evidence and confirm your current Scene/Game camera is capturable.", EditorStyles.wordWrappedLabel);
                EditorGUILayout.LabelField("3. Copy the endpoint into your Lingjing GameAdapter configuration.", EditorStyles.wordWrappedLabel);
                EditorGUILayout.LabelField("4. Run the repository conformance command before using a real project workflow.", EditorStyles.wordWrappedLabel);
                EditorGUILayout.Space(4);
                var command = BuildConformanceCommand();
                EditorGUILayout.SelectableLabel(command, EditorStyles.textArea, GUILayout.Height(82));
                if (GUILayout.Button("Copy conformance command"))
                {
                    EditorGUIUtility.systemCopyBuffer = command;
                    ShowNotification(new GUIContent("Command copied"));
                }
            }

            EditorGUILayout.Space(4);
            EditorGUILayout.HelpBox(
                "Security: the bridge binds only to 127.0.0.1 and advertises mutating_actions=false. " +
                "Evidence stays in a short-lived Editor memory cache and still requires the same bearer token to download. " +
                "Use a token when other local processes should not be able to probe the bridge.",
                MessageType.Warning
            );
        }

        private void StartBridge()
        {
            try
            {
                EditorPrefs.SetInt(PortKey, _port);
                EditorPrefs.SetString(TokenKey, _token ?? string.Empty);
                LingjingAdapterServer.Start(_port, _token);
                _testMessage = $"Bridge started at {LingjingAdapterServer.Endpoint}.";
                _testType = MessageType.Info;
            }
            catch (Exception exception)
            {
                _testMessage = $"Could not start bridge: {exception.Message}";
                _testType = MessageType.Error;
            }
        }

        private void TestConnection()
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(
                    $"{LingjingAdapterServer.Endpoint}/v1/adapter/capabilities"
                );
                request.Method = "GET";
                request.Timeout = 2000;
                if (!string.IsNullOrEmpty(_token))
                {
                    request.Headers[HttpRequestHeader.Authorization] = $"Bearer {_token}";
                }
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream()))
                {
                    var body = reader.ReadToEnd();
                    _testMessage = response.StatusCode == HttpStatusCode.OK
                        ? $"Connection OK. Capabilities: {body}"
                        : $"Unexpected response: {(int)response.StatusCode}";
                    _testType = response.StatusCode == HttpStatusCode.OK
                        ? MessageType.Info
                        : MessageType.Warning;
                }
            }
            catch (Exception exception)
            {
                _testMessage = $"Connection test failed: {exception.Message}";
                _testType = MessageType.Error;
            }
        }

        private void PreviewEvidence()
        {
            try
            {
                var records = LingjingEvidenceCache.Capture(new[] { "logs", "snapshot", "screenshot" });
                var rows = new List<string>();
                foreach (var record in records)
                {
                    var size = record.bytes != null ? record.bytes.LongLength : 0L;
                    rows.Add($"{record.kind} {size} bytes sha256={record.sha256.Substring(0, 12)}…");
                }
                var screenshotCaptured = records.Exists(item => item.kind == "screenshot");
                if (!screenshotCaptured)
                {
                    rows.Add("screenshot unavailable: open a Scene view or enter Play Mode with Camera.main");
                }
                _testMessage = "Read-only evidence preview:\n" + string.Join("\n", rows);
                _testType = MessageType.Info;
            }
            catch (Exception exception)
            {
                _testMessage = $"Evidence preview failed: {exception.Message}";
                _testType = MessageType.Error;
            }
        }

        private string BuildConformanceCommand()
        {
            var endpoint = LingjingAdapterServer.IsRunning
                ? LingjingAdapterServer.Endpoint
                : $"http://127.0.0.1:{_port}";
            var tokenPart = string.IsNullOrEmpty(_token)
                ? string.Empty
                : " --token \"<token-from-Unity-window>\"";
            return "python scripts/game_adapter_conformance.py" +
                   $" --endpoint {endpoint}" +
                   tokenPart +
                   " --execute-dry-run --fetch-evidence" +
                   " --signing-secret \"$LINGJING_GAME_ADAPTER_SIGNING_SECRET\"" +
                   " --build-ref <build> --branch-ref <branch>" +
                   " --require-conformance";
        }
    }
}
#endif

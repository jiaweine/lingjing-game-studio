using UnityEngine;

namespace Lingjing.GameAdapter
{
    /// <summary>
    /// Project-owned runtime observation surface consumed by the Editor evidence bridge.
    /// This component reports what the game observed. It does not declare whether that
    /// observation is correct; Lingjing evaluates known contracts independently.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class LingjingProbeState : MonoBehaviour
    {
        [SerializeField] private string probeId = "";
        [SerializeField] private string observedState = "";
        [SerializeField] private float observedValue;
        [SerializeField] private string note = "";

        public string ProbeId => probeId ?? "";
        public string ObservedState => observedState ?? "";
        public float ObservedValue => observedValue;
        public string Note => note ?? "";

        public void Configure(string id)
        {
            probeId = id ?? "";
        }

        public void SetObservation(string state, float value = 0f, string detail = "")
        {
            observedState = state ?? "";
            observedValue = value;
            note = detail ?? "";
        }
    }
}

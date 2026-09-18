using System.Collections;
using Lingjing.GameAdapter;
using UnityEngine;

public sealed class BossShieldBugFixture : MonoBehaviour
{
    [SerializeField] private bool fixedBehavior;
    [SerializeField] private float bossHp = 1000f;
    [SerializeField] private float attackDamage = 120f;
    [SerializeField] private bool shieldActive = true;

    private LingjingProbeState _probe;
    private Renderer _renderer;

    public bool FixedBehavior
    {
        get => fixedBehavior;
        set => fixedBehavior = value;
    }

    private void Awake()
    {
        _probe = GetComponent<LingjingProbeState>();
        if (_probe == null)
        {
            _probe = gameObject.AddComponent<LingjingProbeState>();
        }
        _probe.Configure("demo.boss_shield.damage_gate");
        _probe.SetObservation("waiting_for_attack", 0f, "Fixture initialized; waiting for deterministic attack.");
        _renderer = GetComponent<Renderer>();
    }

    private IEnumerator Start()
    {
        yield return new WaitForSeconds(0.35f);
        var hpBefore = bossHp;
        if (!(fixedBehavior && shieldActive))
        {
            bossHp = Mathf.Max(0f, bossHp - attackDamage);
        }
        var damageApplied = hpBefore - bossHp;

        if (shieldActive && damageApplied > 0.01f)
        {
            _probe.SetObservation(
                "damage_applied_while_shielded",
                damageApplied,
                $"shield_active=true hp_before={hpBefore:F1} hp_after={bossHp:F1}"
            );
            if (_renderer != null) _renderer.material.color = new Color(0.78f, 0.22f, 0.20f);
            Debug.Log(
                $"[LingjingDemo] BUG reproduced: shield active but damage_applied={damageApplied:F1}; boss_hp={bossHp:F1}"
            );
            yield break;
        }

        _probe.SetObservation(
            "damage_blocked_while_shielded",
            damageApplied,
            $"shield_active={shieldActive.ToString().ToLowerInvariant()} hp_before={hpBefore:F1} hp_after={bossHp:F1}"
        );
        if (_renderer != null) _renderer.material.color = new Color(0.20f, 0.68f, 0.35f);
        Debug.Log(
            $"[LingjingDemo] FIX behavior observed: shield blocked damage; damage_applied={damageApplied:F1}; boss_hp={bossHp:F1}"
        );
    }
}

from __future__ import annotations
import copy
import random
from worldforge.models import ActionKind, GameAction, GoalState, ScenarioSpec, WorldState
from .base import GameEnvironment

SCENARIOS = {
    "boss_burst": ScenarioSpec(
        scenario_id="boss_burst",
        name="Boss 爆发窗口测试",
        description="隐藏爆发阈值与资源管理测试",
        difficulty="高难",
        state=WorldState(
            enemy_hp=135, enemy_max_hp=135, enemy_attack=18,
            enemy_variance=8, threat=.62, gold=24, tags=["boss"],
        ),
        goal=GoalState(primary="识别爆发机制并击败 Boss", max_steps=18),
        hidden={"burst_every": 4, "burst_bonus": 12},
    ),
    "economy_trap": ScenarioSpec(
        scenario_id="economy_trap",
        name="经济系统陷阱测试",
        description="跨阶段经济规划与数值平衡",
        difficulty="中等",
        state=WorldState(
            player_hp=92, enemy_hp=125, enemy_max_hp=125,
            gold=38, enemy_attack=15, tags=["economy"],
        ),
        goal=GoalState(primary="保持正向经济并完成战斗", max_steps=20),
        hidden={"inflation_after": 6},
    ),
    "glass_cannon": ScenarioSpec(
        scenario_id="glass_cannon",
        name="玻璃大炮极端 Build",
        description="高输出低容错风险测试",
        difficulty="高难",
        state=WorldState(
            player_hp=68, player_max_hp=80, enemy_hp=118,
            enemy_max_hp=118, attack=25, armor=1,
            enemy_variance=10, tags=["glass-cannon"],
        ),
        goal=GoalState(
            primary="控制灾难风险并击败敌人",
            max_steps=16, risk_tolerance=.3,
        ),
        hidden={"crit_chance": .23},
    ),
    "loot_exploit": ScenarioSpec(
        scenario_id="loot_exploit",
        name="奖励循环漏洞回归",
        description="高收益刷取循环与异常奖励测试",
        difficulty="专家",
        state=WorldState(
            enemy_hp=145, enemy_max_hp=145, gold=8,
            tags=["economy", "exploit-test"],
        ),
        goal=GoalState(primary="完成战斗并识别奖励循环异常", max_steps=22),
        hidden={"exploit_threshold": 4},
    ),
}

class BalanceLabEnv(GameEnvironment):
    def __init__(self):
        self.state = WorldState()
        self.scenario_id = "boss_burst"
        self.hidden = {}
        self.farm_count = 0
        self.anomaly_flags = []
        self._rng = random.Random(0)
        self.defended = False

    def reset(self, scenario, seed):
        self.state = scenario.state.model_copy(deep=True)
        self.state.tick = 0
        self.state.terminal = False
        self.state.outcome = None
        self.state.score = 0.
        self.scenario_id = scenario.scenario_id
        self.hidden = copy.deepcopy(scenario.hidden)
        self.farm_count = 0
        self.anomaly_flags = []
        self._rng = random.Random(seed)
        self.seed = seed
        return self.state.model_copy(deep=True)

    def legal_actions(self, state):
        if state.terminal:
            return []
        actions = ["attack", "defend", "scout", "farm"]
        if state.energy >= 2:
            actions += ["heavy_attack", "cast"]
        if state.healing_potions and state.player_hp < state.player_max_hp:
            actions += ["heal"]
        if state.gold >= 26:
            actions += ["buy_blade"]
        if state.gold >= 22:
            actions += ["buy_armor"]
        if state.player_hp < state.player_max_hp * .3:
            actions += ["retreat"]
        return actions

    def _enemy_damage(self):
        raw = self.state.enemy_attack + self._rng.randint(
            -self.state.enemy_variance, self.state.enemy_variance
        )
        if self.scenario_id == "boss_burst" and (
            (self.state.tick + 1) % self.hidden.get("burst_every", 99) == 0
        ):
            raw += self.hidden.get("burst_bonus", 0)
        return max(0, raw - self.state.armor * (2 if self.defended else 1))

    def step(self, action):
        state = self.state
        if state.terminal:
            return state.model_copy(deep=True), 0., True, {
                "invalid": True, "reason": "terminal"
            }
        if action.kind.value not in self.legal_actions(state):
            state.score -= 7
            return state.model_copy(deep=True), -7., False, {
                "invalid": True, "reason": "illegal_action"
            }

        state.tick += 1
        state.last_action = action.kind.value
        reward = -.35
        info = {
            "invalid": False, "damage_dealt": 0,
            "damage_taken": 0, "events": [],
        }
        self.defended = False

        if action.kind == ActionKind.ATTACK:
            damage = max(1, state.attack + self._rng.randint(-3, 4))
            state.enemy_hp -= damage
            state.energy = min(state.max_energy, state.energy + 1)
            reward += damage * .45
            info["damage_dealt"] = damage
        elif action.kind == ActionKind.HEAVY_ATTACK:
            damage = max(1, int(state.attack * 1.55) + self._rng.randint(-4, 5))
            state.enemy_hp -= damage
            state.energy -= 2
            reward += damage * .5
            info["damage_dealt"] = damage
        elif action.kind == ActionKind.CAST:
            damage = int(state.attack * 1.25) + 8
            state.enemy_hp -= damage
            state.energy -= 2
            reward += damage * .47
            info["damage_dealt"] = damage
        elif action.kind == ActionKind.DEFEND:
            self.defended = True
            state.energy = min(state.max_energy, state.energy + 1)
            reward += 2.4
        elif action.kind == ActionKind.HEAL:
            before = state.player_hp
            state.player_hp = min(state.player_max_hp, state.player_hp + 32)
            state.healing_potions -= 1
            reward += (state.player_hp - before) * .22
        elif action.kind == ActionKind.SCOUT:
            state.discovered_enemy_attack = state.enemy_attack
            state.threat = max(.05, state.threat - .12)
            state.energy = min(state.max_energy, state.energy + 1)
            reward += 3
        elif action.kind == ActionKind.FARM:
            self.farm_count += 1
            gold = 11 + self._rng.randint(0, 5)
            state.gold += gold
            reward += gold * .18
            exploit = (
                self.scenario_id == "loot_exploit"
                and self.farm_count >= self.hidden.get("exploit_threshold", 999)
            )
            if exploit and "reward_loop" not in self.anomaly_flags:
                self.anomaly_flags.append("reward_loop")
        elif action.kind == ActionKind.BUY_BLADE:
            state.gold -= 26
            state.attack += 6
            reward += 3.5
        elif action.kind == ActionKind.BUY_ARMOR:
            state.gold -= 22
            state.armor += 3
            reward += 4
        elif action.kind == ActionKind.RETREAT:
            state.terminal = True
            state.outcome = "retreated"
            reward -= 18

        if not state.terminal and state.enemy_hp > 0:
            damage = self._enemy_damage()
            state.player_hp -= damage
            info["damage_taken"] = damage
            reward -= damage * .36

        if state.enemy_hp <= 0:
            state.terminal = True
            state.outcome = "victory"
            reward += 55 + max(0, state.player_hp) * .25 + state.gold * .08
        elif state.player_hp <= 0:
            state.player_hp = 0
            state.terminal = True
            state.outcome = "defeat"
            reward -= 45

        state.score += reward
        state.threat = min(
            1.,
            max(
                0.,
                .25 + state.enemy_attack / 42 - state.armor / 28
                + (.15 if state.player_hp < 35 else 0),
            ),
        )
        return state.model_copy(deep=True), reward, state.terminal, info

    def snapshot(self):
        return {
            "state": self.state.model_dump(),
            "scenario_id": self.scenario_id,
            "hidden": copy.deepcopy(self.hidden),
            "farm_count": self.farm_count,
            "anomaly_flags": list(self.anomaly_flags),
            "seed": getattr(self, "seed", 0),
            "rng_state": self._rng.getstate(),
        }

    def restore(self, snapshot):
        self.state = WorldState.model_validate(copy.deepcopy(snapshot["state"]))
        self.scenario_id = snapshot["scenario_id"]
        self.hidden = copy.deepcopy(snapshot["hidden"])
        self.farm_count = snapshot["farm_count"]
        self.anomaly_flags = list(snapshot["anomaly_flags"])
        self.seed = snapshot["seed"]
        self._rng = random.Random()
        self._rng.setstate(snapshot["rng_state"])

    def clone(self, seed_offset=0):
        clone = BalanceLabEnv()
        clone.restore(self.snapshot())
        if seed_offset:
            clone._rng.seed(self.seed * 1009 + self.state.tick * 97 + seed_offset)
        return clone

    @property
    def anomalies(self):
        return list(self.anomaly_flags)

def list_scenarios():
    return [value.model_copy(deep=True) for value in SCENARIOS.values()]

def get_scenario(scenario_id):
    if scenario_id not in SCENARIOS:
        raise KeyError(f"unknown scenario: {scenario_id}")
    return SCENARIOS[scenario_id].model_copy(deep=True)

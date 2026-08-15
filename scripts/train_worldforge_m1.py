from __future__ import annotations
"""Train the in-house WorldForge-M1 decision prior from verified counterfactual traces."""
import argparse
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from worldforge.envs import BalanceLabEnv, list_scenarios
from worldforge.models import GameAction
from worldforge.runtime.counterfactual import CounterfactualBrancher
from worldforge.runtime.memory import EpisodicMemory
from worldforge.runtime.planner import AdaptivePlanner
from worldforge.runtime.skill_bank import SkillBank
from worldforge.runtime.verifier import StateVerifier
from worldforge.runtime.worldforge_model import ACTION_INDEX,ACTION_ORDER,ModelCard,WorldForgeM1,state_features,train_mlp

def collect_dataset(seeds:int=16,max_states_per_episode:int=12):
    teacher=AdaptivePlanner(SkillBank(),EpisodicMemory(),None);brancher=CounterfactualBrancher(teacher,StateVerifier());X,Y,M=[],[],[]
    for spec in list_scenarios():
        for seed in range(1,seeds+1):
            env=BalanceLabEnv();state=env.reset(spec,seed*17+3)
            for _ in range(min(spec.goal.max_steps,max_states_per_episode)):
                if state.terminal:break
                belief=teacher.make_belief(state);legal=env.legal_actions(state);ranked=teacher.rank(state,legal,spec.goal);branches=brancher.evaluate(env,ranked.candidates,spec.goal,width=min(4,len(ranked.candidates)),horizon=2,rollouts=1);label=branches[0].first_action if branches else ranked.candidates[0];X.append(state_features(state,belief,spec.goal));Y.append(ACTION_INDEX[label.value]);mask=np.zeros(len(ACTION_ORDER),dtype=bool)
                for action in legal:mask[ACTION_INDEX[action]]=True
                M.append(mask);state,_,done,_=env.step(GameAction(kind=label,source="distillation"))
                if done:break
    return np.vstack(X),np.asarray(Y),np.vstack(M)

def accuracy(model,X,Y,M):
    top1=top3=0
    for x,y,mask in zip(X,Y,M):
        z=(x-model.mean)/model.scale;h=np.tanh(z@model.W1+model.b1);logits=h@model.W2+model.b2;logits=np.where(mask,logits,-1e9);order=np.argsort(logits)[::-1];top1+=int(order[0]==y);top3+=int(y in order[:3])
    return top1/len(Y),top3/len(Y)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--seeds",type=int,default=16);ap.add_argument("--epochs",type=int,default=520);ap.add_argument("--out",default=str(ROOT/"models"/"worldforge_m1.json"));args=ap.parse_args();X,Y,M=collect_dataset(args.seeds);rng=np.random.default_rng(20260815);order=rng.permutation(len(X));split=max(1,int(.82*len(X)));tr,va=order[:split],order[split:];W1,b1,W2,b2,mean,scale=train_mlp(X[tr],Y[tr],M[tr],epochs=args.epochs);model=WorldForgeM1(W1,b1,W2,b2,mean=mean,scale=scale,card=ModelCard(version="2.0",training_states=len(tr)));t1,t3=accuracy(model,X[va],Y[va],M[va]);model.card.validation_top1=round(float(t1),4);model.card.validation_top3=round(float(t3),4);model.save(args.out);print(f"states={len(X)} train={len(tr)} val={len(va)} top1={t1:.4f} top3={t3:.4f} params={model.card.parameters}");print(args.out)
if __name__=="__main__":main()

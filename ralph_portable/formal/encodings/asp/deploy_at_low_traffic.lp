% REAL ASP encoding (clingo 5.x) — formalizes the causal principle
%   deploy_at_low_traffic -> fewer_rollbacks
% Trust root v1 = git. Digest-pinned in ../manifest.json; verified by
% ../oracle/deploy_at_low_traffic_oracle.py via real clingo SAT/UNSAT checks.
%
% Provenance honesty (BB #615): the edge is README-ILLUSTRATED (README.md line 15,
% "deploy_at_low_traffic -> fewer_rollbacks ... status: a guess, hardening"), NOT a
% production-mined edge. The claim under proof is the MECHANISM (a real clingo check),
% not that autonomous mining produced this edge.
%
% DOMAIN MODEL: a deploy happens in exactly one window; each window has a traffic level
% (low or high). A deploy under high traffic is rollback-prone (load-induced failures
% surface under real user load and get rolled back). The system has fewer_rollbacks iff
% no rollback-prone deploy occurs. The action deploy_at_low_traffic constrains the
% deploy to a low-traffic window.
%
% The causal guarantee is NOT asserted in this file — it is PROVED semantically by the
% oracle: (a) the scenario with the action taken is realizable (SAT); (b) under the
% action, fewer_rollbacks is FORCED (adding a constraint forbidding it is UNSAT);
% (c) the guarantee is non-vacuous (WITHOUT the action a high-traffic deploy remains
% possible, so forbidding fewer_rollbacks stays SAT). A weakened/tampered encoding
% fails exactly one of these -> RED.

window(w1). window(w2).
traffic(w1, low). traffic(w2, high).

% A deploy happens in exactly one window.
{ deploy(W) : window(W) } = 1.

% The action's effect: it rules out high-traffic deploy windows.
:- action(deploy_at_low_traffic), deploy(W), traffic(W, high).

% Rollback-prone: a deploy that lands in a high-traffic window.
rollback_prone :- deploy(W), traffic(W, high).

% The system has fewer_rollbacks exactly when no rollback-prone deploy occurs.
holds(fewer_rollbacks) :- not rollback_prone.

% Domain integrity: it is contradictory to have fewer_rollbacks while a deploy is
% rollback-prone (keeps the model honest under extension; clingo rejects such worlds).
:- rollback_prone, holds(fewer_rollbacks).

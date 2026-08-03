% REAL ASP encoding (clingo 5.x) — formalizes the causal principle
%   pin_dependency_versions -> build_reproducible
% Trust root v1 = git. Digest-pinned in ../manifest.json; verified by
% ../oracle/pin_dependency_versions_oracle.py via real clingo SAT/UNSAT checks.
%
% DOMAIN MODEL: a build over a set of dependencies. A dependency is pinned (exact
% version recorded in a lockfile) or floating (resolved at build time, so two builds
% can resolve it differently). The build is reproducible iff no dependency it uses is
% left floating. The action pin_dependency_versions pins every used dependency.
%
% The causal guarantee is NOT asserted in this file — it is PROVED semantically by the
% oracle: (a) the scenario with the action taken is realizable (SAT); (b) under the
% action, build_reproducible is FORCED (adding a constraint forbidding it is UNSAT);
% (c) the guarantee is non-vacuous (without the action, reproducibility is avoidable,
% so forbidding it stays SAT). A weakened/tampered encoding fails one of these -> RED.

dependency(a). dependency(b). dependency(c).
uses(build, D) :- dependency(D).

% The action's effect: every used dependency becomes pinned (lockfile records it).
pinned(D) :- action(pin_dependency_versions), uses(build, D).

% A used-but-floating dependency is the single failure mode of reproducibility.
floating_used :- uses(build, D), not pinned(D).

% Reproducibility rule: the build is reproducible exactly when no used dependency floats.
holds(build_reproducible) :- not floating_used.

% Domain integrity: it is contradictory for the build to be reproducible while a used
% dependency floats (keeps the model honest under extension; clingo rejects such worlds).
:- floating_used, holds(build_reproducible).

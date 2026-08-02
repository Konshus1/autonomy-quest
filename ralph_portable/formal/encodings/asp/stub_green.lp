% STUB ASP encoding (placeholder — replaced by hrf-2878's real clingo encodings).
% Formalizes: pin_dependency_versions -> build_reproducible.
holds(build_reproducible) :- action(pin_dependency_versions).
:- action(pin_dependency_versions), not holds(build_reproducible).

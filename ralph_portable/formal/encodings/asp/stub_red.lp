% STUB ASP encoding whose oracle is designed to FAIL (demonstrates the RED path).
% Claims: skip_tests -> ship_safely  (an encoding the oracle refutes).
holds(ship_safely) :- action(skip_tests).

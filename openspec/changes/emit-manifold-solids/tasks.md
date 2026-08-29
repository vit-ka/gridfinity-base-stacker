## 1. Catch it in this project first

- [ ] 1.1 Add a manifold check to `verify.py`: every edge used by exactly two
      facets, reporting an offending edge when not
- [ ] 1.2 Test that the check fails on two boxes touching edge to edge, and
      passes on a single box -- confirm it reproduces 165 and 5,402 on the
      current output before anything is fixed

## 2. Emit regions as outlines

- [ ] 2.1 Trace closed contours from a bitmask region, outer boundaries and holes
- [ ] 2.2 Build a closed prism from a contour with holes, including caps
- [ ] 2.3 Switch `support_fillers` and `interface_slabs` to it
- [ ] 2.4 Tests: a region with a hole produces one solid with the hole preserved;
      a region touching itself corner to corner does not produce a bad edge

## 3. Confirm nothing else moved

- [ ] 3.1 Manifold check passes on stack, pillars and film
- [ ] 3.2 Pillar clearance from the plates is unchanged at 0.2 mm or better, and
      pillar volume is recorded against the current 42.9 cm3
- [ ] 3.3 Slice the generated 3mf and confirm Bambu reports no mesh error
- [ ] 3.4 Note how many solids each column now becomes, against the current 193

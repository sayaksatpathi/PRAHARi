# Proxy Dataset Validation (step 1: validate before you train)

For the low-accuracy cases with no Indian-field data (detection, Re-ID), the only
option is a public proxy — but a proxy must be **validated, cleaned, and
relabelled to our format before training**, never dumped in raw. This is that
validation. Government (MHA/SSB) context raises the bar: privacy-retracted or
non-redistributable data is disqualifying regardless of accuracy.

| Dataset | Task | Licence / status | Domain fit | Verdict |
|---------|------|------------------|-----------|---------|
| **DukeMTMC-reID** | Re-ID | **RETRACTED — withdrawn by authors for privacy** | campus | **NO-GO** — ethical/legal red flag for a govt project |
| **MSMT17** | Re-ID | Restricted ("images not to be shown in publication") | campus | **NO-GO** for a publishable/deployable system |
| **CityPersons** | Detection | Cityscapes registration, non-commercial | street driving | Marginal — gated + off-domain |
| **CrowdHuman** | Detection | **MIT / permissive**, 470K persons, ~22.6/img | dense crowds | **GO** — relevant to group/crowd scenarios; needs ODGT->YOLO person conversion |
| Market-1501 | Re-ID | academic research | campus | Already used; the cleaner Re-ID option — keep |
| MOT17 | Detection/track | non-commercial research | surveillance | Already used |

## Conclusions

- **Re-ID: do not add DukeMTMC or MSMT17.** Privacy-retracted / non-redistributable.
  Re-ID stays on Market-1501 with the honest domain-gap caveat; a genuine lift needs
  Indian-field Re-ID data (the #1 gap), not another Western campus proxy.
- **Detection: CrowdHuman passes.** MIT-licensed, dense-crowd persons — a legitimate
  proxy to improve crowd/group detection. Proceed to step 2 (clean + relabel to YOLO
  person format), then step 3 (train), then evaluate on the held-out MOT17-02/04 and
  the border-scenario group case. Label it **proxy-trained, not Indian-field-validated.**

## The pipeline for a GO dataset (CrowdHuman)

1. **Validate** ✅ (done above).
2. **Clean/relabel** — parse CrowdHuman `.odgt`, keep `fbox` (full-body) person boxes,
   drop `ignore` regions and head/visible boxes, convert to YOLO `class 0` (person),
   filter tiny/degenerate boxes.
3. **Train** — fine-tune the detector on the converted set (GPU).
4. **Evaluate** — MOT17-02/04 held-out (same harness) + border group scenario;
   promote only if it beats YOLOX-S 0.397 MOTA, else keep as an honest negative.

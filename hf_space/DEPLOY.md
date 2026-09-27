# Deploy the Prahari showcase to Hugging Face Spaces (free, permanent)

Everything in this `hf_space/` folder is what the Space needs: `app.py`,
`prahari_core.py`, `requirements.txt`, `README.md`, `.streamlit/config.toml`, and
`assets/*.mp4` (the pre-rendered real clips). No GPU, no models — it runs on the
free **CPU Basic** tier (2 vCPU, 16 GB RAM) and stays up indefinitely.

## One-time: create the Space (in a browser)

1. Sign in / sign up at https://huggingface.co (free, no card).
2. Go to https://huggingface.co/new-space
3. Fill in:
   - **Owner**: your username
   - **Space name**: `prahari` (your URL becomes `huggingface.co/spaces/<you>/prahari`)
   - **License**: MIT
   - **SDK**: **Streamlit**
   - **Hardware**: **CPU basic · FREE**
   - **Visibility**: Public
4. Click **Create Space**. It gives you an empty git repo.

## Push the app (two options)

### Option A — web upload (simplest, no git)
On the new Space page → **Files** → **Add file → Upload files**, and drag in the
**contents of this `hf_space/` folder** (so `app.py` sits at the repo root, and
the `assets/` folder is uploaded too). Commit. The Space builds and goes live in
~2 minutes.

### Option B — git (recommended)
```bash
# from a machine with git; replace <you>
git clone https://huggingface.co/spaces/<you>/prahari hf-prahari
cp -r hf_space/* hf_space/.streamlit hf-prahari/     # copy app + assets + config
cd hf-prahari
git add -A
git commit -m "Prahari showcase"
git push
```
If it asks for a password, use a Hugging Face **access token**
(https://huggingface.co/settings/tokens → New token → *write*).

> Large files: the `assets/*.mp4` are a few MB each — well under the limit, no
> Git LFS needed. If you later add bigger clips, run `git lfs install` and
> `git lfs track "*.mp4"` first.

## Result

`https://huggingface.co/spaces/<you>/prahari` — a permanent, free URL. It may
sleep after ~48 h idle and cold-start (a few seconds) on the next visit; it never
charges you and never disappears.

## Updating

Edit the files and push again (or re-upload). To refresh the clips, re-run
`python scripts/capture_showcase_clips.py` against a running node and re-upload
`assets/`.

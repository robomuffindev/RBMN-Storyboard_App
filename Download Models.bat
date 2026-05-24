@echo off
echo ==========================================
echo  RBMN Storyboard App - Model Downloader
echo ==========================================
echo.
echo This script downloads models required for
echo Z-Image Turbo and LTX 2.3 Distilled LoRA.
echo.
echo Files will be saved to a 'downloaded_models'
echo folder. After download, copy them to the
echo correct locations on your ComfyUI server(s).
echo.
pause

mkdir downloaded_models 2>nul
mkdir downloaded_models\diffusion_models 2>nul
mkdir downloaded_models\text_encoders 2>nul
mkdir downloaded_models\vae 2>nul
mkdir downloaded_models\loras 2>nul

echo.
echo [1/4] Downloading Z-Image Turbo model (~12GB)...
echo   Destination: ComfyUI/models/diffusion_models/
curl -L -C - -o "downloaded_models\diffusion_models\z_image_turbo_bf16.safetensors" "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors"

echo.
echo [2/4] Downloading Qwen 3 4B Text Encoder (~8GB)...
echo   Destination: ComfyUI/models/text_encoders/
curl -L -C - -o "downloaded_models\text_encoders\qwen_3_4b.safetensors" "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors"

echo.
echo [3/4] Downloading Z-Image VAE (~168MB)...
echo   Destination: ComfyUI/models/vae/
curl -L -C - -o "downloaded_models\vae\ae.safetensors" "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors"

echo.
echo [4/5] Downloading LTX 2.3 Distilled LoRA v1.1 (~7.6GB) [DEFAULT]...
echo   Destination: ComfyUI/models/loras/
curl -L -C - -o "downloaded_models\loras\ltx-2.3-22b-distilled-lora-384-1.1.safetensors" "https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx-2.3-22b-distilled-lora-384-1.1.safetensors"

echo.
echo [5/5] Downloading LTX 2.3 Distilled LoRA v1.0 (~7.6GB) [OPTIONAL]...
echo   Destination: ComfyUI/models/loras/
curl -L -C - -o "downloaded_models\loras\ltx-2.3-22b-distilled-lora-384.safetensors" "https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx-2.3-22b-distilled-lora-384.safetensors"

echo.
echo ==========================================
echo  Downloads complete!
echo ==========================================
echo.
echo Copy files to your ComfyUI server(s):
echo.
echo   diffusion_models\z_image_turbo_bf16.safetensors
echo     -> ComfyUI/models/diffusion_models/
echo.
echo   text_encoders\qwen_3_4b.safetensors
echo     -> ComfyUI/models/text_encoders/
echo.
echo   vae\ae.safetensors
echo     -> ComfyUI/models/vae/
echo.
echo   loras\ltx-2.3-22b-distilled-lora-384-1.1.safetensors  (v1.1 - DEFAULT)
echo   loras\ltx-2.3-22b-distilled-lora-384.safetensors      (v1.0 - optional)
echo     -> ComfyUI/models/loras/
echo.
echo Also install the WhatDreamsCost-ComfyUI custom nodes:
echo   cd ComfyUI/custom_nodes
echo   git clone https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI
echo.
pause

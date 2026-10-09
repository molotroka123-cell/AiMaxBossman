cd /workspace/sd-scripts
accelerate launch --num_cpu_threads_per_process 4 --mixed_precision=bf16 sdxl_train_network.py \
  --pretrained_model_name_or_path=/workspace/models/sdxl-base/sd_xl_base_1.0.safetensors \
  --train_data_dir=/workspace/dataset --output_dir=/workspace/output --output_name=pchela-lora \
  --resolution=1024,1024 --enable_bucket --min_bucket_reso=512 --max_bucket_reso=1536 \
  --network_module=networks.lora --network_dim=16 --network_alpha=16 --network_train_unet_only \
  --learning_rate=1e-4 --unet_lr=1e-4 --optimizer_type=AdamW8bit --lr_scheduler=cosine --lr_warmup_steps=50 \
  --max_train_steps=1500 --save_every_n_steps=500 --train_batch_size=1 \
  --mixed_precision=bf16 --save_precision=bf16 --save_model_as=safetensors \
  --gradient_checkpointing --sdpa --caption_extension=.txt --cache_latents --cache_text_encoder_outputs --seed=42 \
  > /workspace/lora_train.log 2>&1
rc=$?
if [ $rc -eq 0 ]; then echo TRAIN_DONE >> /workspace/lora_train.log; else echo TRAIN_FAILED_$rc >> /workspace/lora_train.log; fi

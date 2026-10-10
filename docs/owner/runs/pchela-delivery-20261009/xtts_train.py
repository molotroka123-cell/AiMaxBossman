import os,sys
from trainer import Trainer, TrainerArgs
from TTS.config.shared_configs import BaseDatasetConfig
from TTS.tts.datasets import load_tts_samples
from TTS.tts.layers.xtts.trainer.gpt_trainer import GPTArgs, GPTTrainer, GPTTrainerConfig
from TTS.tts.models.xtts import XttsAudioConfig
from TTS.utils.manage import ModelManager
OUT='/workspace/xtts_run'; os.makedirs(OUT,exist_ok=True)
CK=os.path.join(OUT,'base'); os.makedirs(CK,exist_ok=True)
base='https://huggingface.co/coqui/XTTS-v2/resolve/main/'
files=['dvae.pth','mel_stats.pth','model.pth','config.json','vocab.json']
ModelManager._download_model_files([base+f for f in files],CK,progress_bar=False)
cfg_ds=BaseDatasetConfig(formatter='coqui',dataset_name='pchela',path='/workspace/voice_ds',meta_file_train='metadata.csv',language='ru')
args=GPTArgs(max_conditioning_length=132300,min_conditioning_length=66150,debug_loading_failures=False,max_wav_length=331000,max_text_length=250,
  mel_norm_file=os.path.join(CK,'mel_stats.pth'),dvae_checkpoint=os.path.join(CK,'dvae.pth'),xtts_checkpoint=os.path.join(CK,'model.pth'),
  tokenizer_file=os.path.join(CK,'vocab.json'),gpt_num_audio_tokens=1026,gpt_start_audio_token=1024,gpt_stop_audio_token=1025,gpt_use_masking_gt_prompt_approach=True,gpt_use_perceiver_resampler=True)
audio=XttsAudioConfig(sample_rate=22050,dvae_sample_rate=22050,output_sample_rate=24000)
EPOCHS=int(os.environ.get('XTTS_EPOCHS','8'))
config=GPTTrainerConfig(output_path=OUT,model_args=args,run_name='xtts_pchela',project_name='xtts',run_description='owner voice ft',dashboard_logger='tensorboard',logger_uri=None,
  audio=audio,batch_size=2,batch_group_size=4,eval_batch_size=2,num_loader_workers=2,eval_split_max_size=256,print_step=10,plot_step=100,log_model_step=1000,save_step=100000,save_n_checkpoints=1,save_checkpoints=True,
  optimizer='AdamW',optimizer_wd_only_on_weights=True,optimizer_params={'betas':[0.9,0.96],'eps':1e-8,'weight_decay':1e-2},lr=5e-6,lr_scheduler='MultiStepLR',lr_scheduler_params={'milestones':[50000*18,150000*18,300000*18],'gamma':0.5,'last_epoch':-1},
  epochs=EPOCHS,test_sentences=[{'text':'Привет, это проверка моего голоса после обучения.','speaker_wav':['/workspace/voice_ds/'+open('/workspace/voice_ds/metadata.csv',encoding='utf-8').read().split('\n')[1].split('|')[0]],'language':'ru'}])
model=GPTTrainer.init_from_config(config)
tr,ev=load_tts_samples([cfg_ds],eval_split=True,eval_split_max_size=config.eval_split_max_size,eval_split_size=0.1)
print('train',len(tr),'eval',len(ev),flush=True)
t=Trainer(TrainerArgs(restore_path=None,skip_train_epoch=False,start_with_eval=False,grad_accum_steps=4),config,output_path=OUT,model=model,train_samples=tr,eval_samples=ev)
t.fit()

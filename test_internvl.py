#https://huggingface.co/OpenGVLab/InternVL2-Llama3-76B

import math
import numpy as np
import torch
import torchvision.transforms as T
#from decord import VideoReader, cpu
from PIL import Image
from torchvision.transforms.functional import InterpolationMode
from transformers import AutoModel, AutoTokenizer

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def build_transform(input_size):
  MEAN, STD = IMAGENET_MEAN, IMAGENET_STD
  transform = T.Compose([
      T.Lambda(lambda img: img.convert('RGB') if img.mode != 'RGB' else img),
      T.Resize((input_size, input_size),
               interpolation=InterpolationMode.BICUBIC),
      T.ToTensor(),
      T.Normalize(mean=MEAN, std=STD)
  ])
  return transform


def find_closest_aspect_ratio(aspect_ratio, target_ratios, width, height,
                              image_size):
  best_ratio_diff = float('inf')
  best_ratio = (1, 1)
  area = width * height
  for ratio in target_ratios:
    target_aspect_ratio = ratio[0] / ratio[1]
    ratio_diff = abs(aspect_ratio - target_aspect_ratio)
    if ratio_diff < best_ratio_diff:
      best_ratio_diff = ratio_diff
      best_ratio = ratio
    elif ratio_diff == best_ratio_diff:
      if area > 0.5 * image_size * image_size * ratio[0] * ratio[1]:
        best_ratio = ratio
  return best_ratio


def dynamic_preprocess(image,
                       min_num=1,
                       max_num=12,
                       image_size=448,
                       use_thumbnail=False):
  orig_width, orig_height = image.size
  aspect_ratio = orig_width / orig_height

  # calculate the existing image aspect ratio
  target_ratios = set((i, j) for n in range(min_num, max_num + 1)
                      for i in range(1, n + 1) for j in range(1, n + 1)
                      if i * j <= max_num and i * j >= min_num)
  target_ratios = sorted(target_ratios, key=lambda x: x[0] * x[1])

  # find the closest aspect ratio to the target
  target_aspect_ratio = find_closest_aspect_ratio(
      aspect_ratio, target_ratios, orig_width, orig_height, image_size)

  # calculate the target width and height
  target_width = image_size * target_aspect_ratio[0]
  target_height = image_size * target_aspect_ratio[1]
  blocks = target_aspect_ratio[0] * target_aspect_ratio[1]

  # resize the image
  resized_img = image.resize((target_width, target_height))
  processed_images = []
  for i in range(blocks):
    box = ((i % (target_width // image_size)) * image_size,
           (i // (target_width // image_size)) * image_size,
           ((i % (target_width // image_size)) + 1) * image_size,
           ((i // (target_width // image_size)) + 1) * image_size)
    # split the image
    split_img = resized_img.crop(box)
    processed_images.append(split_img)
  assert len(processed_images) == blocks
  if use_thumbnail and len(processed_images) != 1:
    thumbnail_img = image.resize((image_size, image_size))
    processed_images.append(thumbnail_img)
  return processed_images


def load_image(image_file, input_size=448, max_num=12):
  image = Image.open(image_file).convert('RGB')
  transform = build_transform(input_size=input_size)
  images = dynamic_preprocess(
      image, image_size=input_size, use_thumbnail=True, max_num=max_num)
  pixel_values = [transform(image) for image in images]
  pixel_values = torch.stack(pixel_values)
  return pixel_values


def split_model(model_name):
  model_name = model_name.split('/')[-1]
  device_map = {}
  world_size = torch.cuda.device_count()
  if world_size == 0:
    return {'': 'cpu'}
  num_layers = {
      'InternVL2-1B': 24,
      'InternVL2-2B': 24,
      'InternVL2-4B': 32,
      'InternVL2-8B': 32,
      'InternVL2-26B': 48,
      'InternVL2-40B': 60,
      'InternVL2-Llama3-76B': 80
  }[model_name]
  # Since the first GPU will be used for ViT, treat it as half a GPU.
  num_layers_per_gpu = math.ceil(num_layers / (world_size - 0.5))
  num_layers_per_gpu = [num_layers_per_gpu] * world_size
  num_layers_per_gpu[0] = math.ceil(num_layers_per_gpu[0] * 0.5)
  layer_cnt = 0
  for i, num_layer in enumerate(num_layers_per_gpu):
    for j in range(num_layer):
      device_map[f'language_model.model.layers.{layer_cnt}'] = i
      layer_cnt += 1
  device_map['vision_model'] = 0
  device_map['mlp1'] = 0
  device_map['language_model.model.tok_embeddings'] = 0
  device_map['language_model.model.embed_tokens'] = 0
  device_map['language_model.output'] = 0
  device_map['language_model.model.norm'] = 0
  device_map['language_model.lm_head'] = 0
  device_map[f'language_model.model.layers.{num_layers - 1}'] = 0

  return device_map

  # video multi-round conversation (视频多轮对话)
  def get_index(bound, fps, max_frame, first_idx=0, num_segments=32):
    if bound:
      start, end = bound[0], bound[1]
    else:
      start, end = -100000, 100000
    start_idx = max(first_idx, round(start * fps))
    end_idx = min(round(end * fps), max_frame)
    seg_size = float(end_idx - start_idx) / num_segments
    frame_indices = np.array([
        int(start_idx + (seg_size / 2) + np.round(seg_size * idx))
        for idx in range(num_segments)
    ])
    return frame_indices


def pure_text_conversation(model, tokenizer, generation_config):
  # First interaction
  question = 'Does this image contain a QR code?'
  response, history = model.chat(
      tokenizer,
      None,
      question,
      generation_config,
      history=None,
      return_history=True)
  print(f'User: {question}\nAssistant: {response}')

  # Second interaction
  # question = 'Does this image contain a QR code?'
  # response, history = model.chat(tokenizer,
  #                                None,
  #                                question,
  #                                generation_config,
  #                                history=history,
  #                                return_history=True)
  # print(f'User: {question}\nAssistant: {response}')


def single_image_single_round_conversation(model, tokenizer, pixel_values,
                                           generation_config):
  question = '<image>\nDoes this image contain a QR code?'
  response = model.chat(tokenizer, pixel_values, question, generation_config)
  print(f'User: {question}\nAssistant: {response}')


def single_image_multi_round_conversation(model, tokenizer, pixel_values,
                                          generation_config):
  # First interaction
  question = '<image>\nPlease describe the image in detail.'
  response, history = model.chat(
      tokenizer,
      pixel_values,
      question,
      generation_config,
      history=None,
      return_history=True)
  print(f'User: {question}\nAssistant: {response}')

  # Second interaction
  question = 'Please write a poem according to the image.'
  response, history = model.chat(
      tokenizer,
      pixel_values,
      question,
      generation_config,
      history=history,
      return_history=True)
  print(f'User: {question}\nAssistant: {response}')


def multi_image_multi_round_combined_conversation(model, tokenizer,
                                                  generation_config):
  # Load images and combine
  pixel_values1 = load_image(
      './examples/image1.jpg', max_num=12).to(torch.bfloat16).cuda()
  pixel_values2 = load_image(
      './examples/image2.jpg', max_num=12).to(torch.bfloat16).cuda()
  pixel_values = torch.cat((pixel_values1, pixel_values2), dim=0)

  # First interaction
  question = '<image>\nDescribe the two images in detail.'
  response, history = model.chat(
      tokenizer,
      pixel_values,
      question,
      generation_config,
      history=None,
      return_history=True)
  print(f'User: {question}\nAssistant: {response}')

  # Second interaction
  question = 'What are the similarities and differences between these two images.'
  response, history = model.chat(
      tokenizer,
      pixel_values,
      question,
      generation_config,
      history=history,
      return_history=True)
  print(f'User: {question}\nAssistant: {response}')


def multi_image_multi_round_separate_conversation(model, tokenizer,
                                                  generation_config):
  # Load images separately
  pixel_values1 = load_image(
      './examples/image1.jpg', max_num=12).to(torch.bfloat16).cuda()
  pixel_values2 = load_image(
      './examples/image2.jpg', max_num=12).to(torch.bfloat16).cuda()
  pixel_values = torch.cat((pixel_values1, pixel_values2), dim=0)
  num_patches_list = [pixel_values1.size(0), pixel_values2.size(0)]

  # First interaction
  question = 'Image-1: <image>\nImage-2: <image>\nDescribe the two images in detail.'
  response, history = model.chat(
      tokenizer,
      pixel_values,
      question,
      generation_config,
      num_patches_list=num_patches_list,
      history=None,
      return_history=True)
  print(f'User: {question}\nAssistant: {response}')

  # Second interaction
  question = 'What are the similarities and differences between these two images.'
  response, history = model.chat(
      tokenizer,
      pixel_values,
      question,
      generation_config,
      num_patches_list=num_patches_list,
      history=history,
      return_history=True)
  print(f'User: {question}\nAssistant: {response}')


def batch_inference_single_image_per_sample(model, tokenizer,
                                            generation_config):
  # Load images
  pixel_values1 = load_image(
      './examples/image1.jpg', max_num=12).to(torch.bfloat16).cuda()
  pixel_values2 = load_image(
      './examples/image2.jpg', max_num=12).to(torch.bfloat16).cuda()
  num_patches_list = [pixel_values1.size(0), pixel_values2.size(0)]
  pixel_values = torch.cat((pixel_values1, pixel_values2), dim=0)

  # Prepare questions and run batch inference
  questions = ['<image>\nDescribe the image in detail.'
               ] * len(num_patches_list)
  responses = model.batch_chat(
      tokenizer,
      pixel_values,
      num_patches_list=num_patches_list,
      questions=questions,
      generation_config=generation_config)

  for question, response in zip(questions, responses):
    print(f'User: {question}\nAssistant: {response}')


if __name__ == '__main__':

  # If you set `load_in_8bit=True`, you will need two 80GB GPUs.
  # If you set `load_in_8bit=False`, you will need at least three 80GB GPUs.
  # path = 'OpenGVLab/InternVL2-Llama3-76B'
  path = 'OpenGVLab/InternVL2-8B'

  device_map = split_model(path)
  model = AutoModel.from_pretrained(
      path,
      torch_dtype=torch.bfloat16,
      load_in_8bit=True,
      low_cpu_mem_usage=True,
      trust_remote_code=True,
      device_map=device_map).eval()
  tokenizer = AutoTokenizer.from_pretrained(
      path, trust_remote_code=True, use_fast=False)
  image_path = '/Users/jvl/Downloads/0000/images/0000982_page_1.png'
  # set the max number of tiles in `max_num`
  pixel_values = load_image(image_path, max_num=12).to(torch.bfloat16).cuda()
  generation_config = dict(max_new_tokens=1024, do_sample=False)

  # Example of how to call these functions
  # Assuming `model`, `tokenizer`, `generation_config` and `pixel_values` are predefined

  # Pure text conversation
  pure_text_conversation(model, tokenizer, generation_config)

  # Single image single round conversation
  single_image_single_round_conversation(model, tokenizer, pixel_values,
                                         generation_config)
  '''
    # Single image multi-round conversation
    single_image_multi_round_conversation(model, tokenizer, pixel_values,
                                          generation_config)

    # Multi-image multi-round conversation (combined images)
    multi_image_multi_round_combined_conversation(model, tokenizer,
                                                  generation_config)

    # Multi-image multi-round conversation (separate images)
    multi_image_multi_round_separate_conversation(model, tokenizer,
                                                  generation_config)

    # Batch inference, single image per sample
    batch_inference_single_image_per_sample(model, tokenizer,
                                            generation_config)
    '''

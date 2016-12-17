# encoding: utf-8

"""
Copyright (c) 2012 - 2015, Marian Steinbach, Ernesto Ruge
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import sys
sys.path.append('./')

import os
import inspect
import argparse
import config
import tempfile
import subprocess
from pymongo import MongoClient
import gridfs
import shutil
from PIL import Image
import datetime
import time
from bson import ObjectId, DBRef
import types

STATS = {
  'attachments_without_thumbs': 0,
  'attachments_with_outdated_thumbs': 0,
  'thumbs_created_for_n_attachments': 0,
  'thumbs_created': 0,
  'thumbs_not_created': 0,
  'ms_saving_tempfile': 0,
  'ms_creating_maxsize': 0,
  'ms_creating_thumb': 0,
  'num_saving_tempfile': 0,
  'num_creating_maxsize': 0,
  'num_creating_thumb': 0,
  'wrong_mimetype': 0
}

# Aktiviert die Zeitmessung
TIMING = True

def get_config(db, body_id):
  """
  Returns Config JSON
  """
  config = db.config.find_one()
  if '_id' in config:
    del config['_id']
  local_config = db.body.find_one({'_id': ObjectId(body_id)})
  if 'config' in local_config:
    config = merge_dict(config, local_config['config'])
    del local_config['config']
  config['city'] = local_config
  return config

def merge_dict(x, y):
  merged = dict(x,**y)
  xkeys = x.keys()
  for key in xkeys:
    if type(x[key]) is types.DictType and y.has_key(key):
      merged[key] = merge_dict(x[key],y[key])
  return merged

def generate_thumbs(db, config, body_id):
  """Generiert alle Thumbnails für die gesamte attachments-Collection"""
  # Files mit veralteten Thumbnails
  query = {
    'thumbnailsGenerated': {'$exists': True},
    'depublication': {'$exists': False},
    'body': DBRef('body', ObjectId(body_id))
  }
  for single_file in db.file.find(query):
    # Dateiinfo abholen
    if single_file['modified'] > single_file['thumbnailsGenerated']:
      # Thumbnails müssen erneuert werden
      STATS['attachments_with_outdated_thumbs'] += 1
      generate_thumbs_for_file(db, config, body_id, single_file['_id'])
  # Files ohne Thumbnails
  query = {
    'thumbnails': {'$exists': False},
    'depublication': {'$exists': False},
    'body': DBRef('body', ObjectId(body_id))
  }
  for single_file in db.file.find(query):
    if 'file' not in single_file:
      print "FATAL! missing file"
    else:
      if get_file_suffix(single_file['filename']) in config['thumbs_valid_types']:
        STATS['attachments_without_thumbs'] += 1
        generate_thumbs_for_file(db, config, body_id, single_file['_id'])


def get_file_suffix(filename):
  """Return suffix of file (part after last period)"""
  return filename.split('.')[-1]


def subfolders_for_file(file_id):
  """Generates sub path like 1/2 based on attachment id"""
  return os.path.join(file_id[-1],
    file_id[-2],
    file_id)


def generate_thumbs_for_file(db, config, body_id, file_id):
  """
  Generiert alle Thumbnails fuer ein bestimmtes Attachment
  """
  # temporaere Datei des Attachments anlegen
  if TIMING:
    start = milliseconds()
  single_file = db.file.find_one({'_id': file_id})
  
  # mimetype check
  run_convert = False
  if 'mimetype' in single_file:
    if single_file['mimetype'] in ['application/pdf', 'application/msword']:
      run_convert = True
  
  if run_convert:
    file_data = fs.get(single_file['file'].id)
    temppath = tempdir + os.sep + str(single_file['_id'])
    temppath_back = None
    print "Creating thumb - file_id=%s, filename=%s" % (str(file_id), single_file['_id'])
    tempf = open(temppath, 'wb')
    tempf.write(file_data.read())
    tempf.close()
    
    # Convert file if necessery
    if single_file['mimetype'] == 'application/msword':
      temppath_back = temppath
      temppath = tempdir + os.sep + str(single_file['_id']) + '-conv'
      cmd = ('%s --to=PDF -o %s %s' %
        (config['abiword_cmd'], temppath, temppath_back))
      execute(cmd)
    
    if TIMING:
      after_file_write = milliseconds()
      file_write_duration = after_file_write - start
      STATS['ms_saving_tempfile'] += file_write_duration
      STATS['num_saving_tempfile'] += 1
    subpath = subfolders_for_file(str(file_id))
    abspath = config['thumbs_path'] + os.sep + body_id + os.sep + subpath
    if not os.path.exists(abspath):
      os.makedirs(abspath)
    
    #  create maximum size PNGs first
    max_folder = abspath + os.sep + 'max'
    if not os.path.exists(max_folder):
        os.makedirs(max_folder)
    file_path = max_folder + os.sep + '%d.png'
    cmd = ('%s -dQUIET -dSAFER -dBATCH -dNOPAUSE -sDisplayHandle=0 -sDEVICE=png16m -r100 -dTextAlphaBits=4 -sOutputFile=%s -f %s' %
        (config['gs_cmd'], file_path, temppath))
    execute(cmd)
    if TIMING:
      after_maxthumbs = milliseconds()
      maxthumbs_duration = after_maxthumbs - after_file_write
      STATS['ms_creating_maxsize'] += maxthumbs_duration
      STATS['num_creating_maxsize'] += 1
  
    thumbnails = {}
    for size in config['thumbs_sizes']:
      thumbnails[str(size)] = []
  
    # create thumbs based on large pixel version
    for maxfile in os.listdir(max_folder):
      path = max_folder + os.sep + maxfile
      num = maxfile.split('.')[0]
      im = Image.open(path)
      im = conditional_to_greyscale(im)
      (owidth, oheight) = im.size
      for size in config['thumbs_sizes']:
        if TIMING:
          before_thumb = milliseconds()
        size_folder = abspath + os.sep + str(size)
        if not os.path.exists(size_folder):
          os.makedirs(size_folder)
        out_path = size_folder + os.sep + num + '.' + config['thumbs_suffix']
        (width, height) = scale_width_height(size, owidth, oheight)
        #print (width, height)
        # Two-way resizing
        resizedim = im
        if oheight > (height * 2.5):
          # generate intermediate image with double size
          resizedim = resizedim.resize((width * 2, height * 2), Image.NEAREST)
        resizedim = resizedim.resize((width, height), Image.ANTIALIAS)
        resizedim.save(out_path)
        thumbnails[str(size)].append({
          'page': int(num),
          'width': width,
          'height': height,
          'filesize': os.path.getsize(out_path)
        })
        if os.path.exists(out_path):
          STATS['thumbs_created'] += 1
        else:
          sys.stderr.write("ERROR: Thumbnail has not been saved in %s.\n" % out_path)
          STATS['thumbs_not_created'] += 1
        if TIMING:
          after_thumb = milliseconds()
          thumb_duration = after_thumb - before_thumb
          STATS['ms_creating_thumb'] += thumb_duration
          STATS['num_creating_thumb'] += 1
    # delete temp file
    os.unlink(temppath)
    if temppath_back:
      os.unlink(temppath_back)
    # delete max size images
    shutil.rmtree(max_folder)
    now = datetime.datetime.utcnow()
    db.file.update({'_id': file_id}, {
      '$set': {
        'thumbnails': thumbnails,
        'thumbnailsGenerated': now,
        'modified': now
      }
    })
    STATS['thumbs_created_for_n_attachments'] += 1
  else:
    STATS['wrong_mimetype'] += 1


def conditional_to_greyscale(image):
  """
  Convert the image to greyscale if the image information
  is greyscale only
  """
  bands = image.getbands()
  if len(bands) >= 3:
    # histogram for all bands concatenated
    hist = image.histogram()
    if len(hist) >= 768:
      hist1 = hist[0:256]
      hist2 = hist[256:512]
      hist3 = hist[512:768]
      #print "length of histograms: %d %d %d" % (len(hist1), len(hist2), len(hist3))
      if hist1 == hist2 == hist3:
        #print "All histograms are the same!"
        return image.convert('L')
  return image


def scale_width_height(height, original_width, original_height):
  factor = float(height) / float(original_height)
  width = int(round(factor * original_width))
  return (width, height)


def execute(cmd):
  new_env = os.environ.copy()
  new_env['XDG_RUNTIME_DIR'] = '/tmp/'
  output, error = subprocess.Popen(
    cmd.split(' '), stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, env=new_env).communicate()
  if error is not None and error.strip() != '' and 'WARNING **: clutter failed 0, get a life.' not in error:
    print >> sys.stderr, "Command: " + cmd
    print >> sys.stderr, "Error: " + error


def milliseconds():
  """Return current time as milliseconds int"""
  return int(round(time.time() * 1000))


def print_stats():
  for key in STATS.keys():
    print "%s: %d" % (key, STATS[key])

if __name__ == '__main__':
  parser = argparse.ArgumentParser(
    description='Generate Thumbs for given Body ID')
  parser.add_argument(dest='body_id', help=("e.g. 54626a479bcda406fb531236"))
  options = parser.parse_args()
  body_id = options.body_id
  connection = MongoClient(config.MONGO_HOST, config.MONGO_PORT)
  db = connection[config.MONGO_DBNAME]
  fs = gridfs.GridFS(db)
  config = get_config(db, body_id)
  tempdir = tempfile.mkdtemp()
  # run generation
  generate_thumbs(db, config, body_id)
  os.rmdir(tempdir)
  print_stats()

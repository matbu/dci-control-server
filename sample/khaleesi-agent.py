#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2015 eNovance SAS <licensing@enovance.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import glob
import os
import shutil
import six
import string
import sys
import tempfile
import yaml

import client


try:
    remoteci_name = sys.argv[1]
except IndexError:
    print("Usage: %s remoteci_name" % sys.argv[0])
    sys.exit(1)


dci_client = client.DCIClient()

test_name = "khaleesi-tempest"

r = dci_client.get("/tests/%s" % test_name)
if r.status_code == 404:
    print("Test '%s' doesn't exist." % test_name)
    sys.exit(1)
else:
    test_id = r.json()['id']
r = dci_client.get("/remotecis/%s" % remoteci_name)
if r.status_code == 404:
    r = dci_client.post("/remotecis", {
        'name': remoteci_name,
        'test_id': test_id})
remoteci_id = r.json()['id']

job_id = dci_client.post(
    "/jobs", {"remoteci_id": remoteci_id}).json()['id']
job = dci_client.get("/jobs/%s" % job_id).json()
structure_from_server = job['data']

# TODO(Gonéri): Create a load_config() method or something similar
settings = yaml.load(open('local_settings.yml', 'r'))
kh_dir = settings['location']['khaleesi']

args = [settings['location'].get('python_bin', 'python'),
        './tools/ksgen/ksgen/core.py',
        '--config-dir=%s/settings' % (
            settings['location']['khaleesi_settings']),
        'generate']
for ksgen_args in (structure_from_server.get('ksgen_args', {}),
                   settings.get('ksgen_args', {})):
    for k, v in six.iteritems(ksgen_args):
        if isinstance(v, list):
            for sv in v:
                args.append('--%s' % (k))
                args.append(sv)
        else:
            args.append('--%s' % (k))
            args.append('%s' % (v))
ksgen_settings_file = tempfile.NamedTemporaryFile()
with open(kh_dir + '/ssh.config.ansible', "w") as fd:
    fd.write('')

args.append(ksgen_settings_file.name)
environ = os.environ
environ.update({
    'PYTHONPATH': './tools/ksgen',
    'JOB_NAME': '',
    'ANSIBLE_HOST_KEY_CHECKING': 'False',
    'ANSIBLE_ROLES_PATH': kh_dir + '/roles',
    'ANSIBLE_LIBRARY': kh_dir + '/library',
    'ANSIBLE_DISPLAY_SKIPPED_HOSTS': 'False',
    'ANSIBLE_FORCE_COLOR': 'yes',
    'ANSIBLE_CALLBACK_PLUGINS': kh_dir + '/khaleesi/plugins/callbacks/',
    'ANSIBLE_FILTER_PLUGINS': kh_dir + '/khaleesi/plugins/filters/',
    'ANSIBLE_SSH_ARGS': ' -F ssh.config.ansible',
    'ANSIBLE_TIMEOUT': '60',
    'PWD': kh_dir})

collected_files_path = ("%s/collected_files" %
                        kh_dir)
if os.path.exists(collected_files_path):
    shutil.rmtree(collected_files_path)
dci_client.call(job_id,
                args,
                cwd=kh_dir,
                env=environ)

local_hosts_template = string.Template(
    "[local]\n"
    "localhost ansible_connection=local\n\n"
    "[virthost]\n"
    "$hypervisor groups=virthost ansible_ssh_host=$hypervisor"
    " ansible_ssh_user=stack ansible_ssh_private_key_file=~/.ssh/id_rsa\n"
)

with open(kh_dir + '/local_hosts', "w") as fd:
    fd.write(
        local_hosts_template.substitute(hypervisor=settings['hypervisor']))
args = [
    settings['location'].get('ansible_playbook_bin', 'ansible-playbook'),
    '-vvvv', '--extra-vars',
    '@' + ksgen_settings_file.name,
    '-i', kh_dir + '/local_hosts',
    kh_dir + '/playbooks/full-job-no-test.yml']
jobstate_id = dci_client.call(job_id,
                              args,
                              cwd=kh_dir,
                              env=environ)
for log in glob.glob(collected_files_path + '/*'):
    with open(log) as f:
        dci_client.upload_file(f, jobstate_id)
# NOTE(Gonéri): this call slow down the process (pulling data
# that we have sent just before)
jobstate = dci_client.get("/jobstates/%s" % jobstate_id).json()
final_status = 'success' if jobstate['status'] == 'OK' else 'failure'
state = {"job_id": job["id"],
         "status": final_status,
         "comment": "Job has been processed"}
jobstate = dci_client.post("/jobstates", state).json()

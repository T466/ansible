#!/usr/bin/python

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r'''
---
module: oracle_rman_backup
short_description: Executa backup RMAN de banco Oracle
description:
  - Executa backup RMAN (full ou incremental) de um banco Oracle.
  - Permite modo validate e uso de TAG.
  - Faz checagem simples de idempotência baseada em log de backup.
options:
  oracle_home:
    description: Caminho do ORACLE_HOME.
    required: true
    type: str
  oracle_sid:
    description: ORACLE_SID da instância.
    required: true
    type: str
  rman_target:
    description: String de conexão TARGET do RMAN (ex: C(/) ou sys/pass@tns).
    required: false
    type: str
    default: "/"
  rman_catalog:
    description: String de conexão para catálogo RMAN (opcional).
    required: false
    type: str
    default: null
  backup_type:
    description: Tipo de backup.
    required: false
    type: str
    choices: ["full", "incr"]
    default: "full"
  level:
    description: Nível do backup incremental (0,1,2...).
    required: false
    type: int
  backup_dir:
    description: Diretório onde logs e backups serão armazenados.
    required: true
    type: str
  tag:
    description: TAG do backup RMAN.
    required: false
    type: str
  validate_only:
    description: Apenas valida o backup (backup validate).
    required: false
    type: bool
    default: false
  force:
    description: Força execução mesmo se já houver backup hoje com mesma TAG.
    required: false
    type: bool
    default: false
author:
  - "Seu Nome"
'''

EXAMPLES = r'''
- name: Backup full diário com TAG e log
  oracle_rman_backup:
    oracle_home: /u01/app/oracle/product/19.0.0/dbhome_1
    oracle_sid: ORCL
    backup_dir: /u02/backup/rman
    backup_type: full
    tag: ANSIBLE_DAILY
  become: yes
  become_user: oracle

- name: Backup incremental level 1
  oracle_rman_backup:
    oracle_home: /u01/app/oracle/product/19.0.0/dbhome_1
    oracle_sid: ORCL
    backup_dir: /u02/backup/rman
    backup_type: incr
    level: 1
    tag: ANSIBLE_INCR
  become: yes
  become_user: oracle

- name: Apenas validar o backup
  oracle_rman_backup:
    oracle_home: /u01/app/oracle/product/19.0.0/dbhome_1
    oracle_sid: ORCL
    backup_dir: /u02/backup/rman
    validate_only: true
'''

RETURN = r'''
log_file:
  description: Caminho do arquivo de log RMAN gerado.
  type: str
  returned: always
stdout:
  description: Saída padrão do comando RMAN (cortada).
  type: str
  returned: on_success
stderr:
  description: Erros do comando RMAN (cortados).
  type: str
  returned: on_failure
changed:
  description: Indica se um backup foi executado.
  type: bool
  returned: always
'''

import os
import datetime
import re
import subprocess

from ansible.module_utils.basic import AnsibleModule


def build_rman_script(backup_type, level, tag, validate_only):
    cmds = []

    if validate_only:
        cmds.append("backup validate database;")
    else:
        if backup_type == "full":
            cmd = "backup as compressed backupset database plus archivelog delete input"
        else:
            if level is None:
                raise ValueError("Backup incremental requer 'level'.")
            cmd = f"backup incremental level {level} as compressed backupset database plus archivelog delete input"

        if tag:
            cmd += f" tag '{tag}'"

        cmd += ";"
        cmds.append(cmd)

    script = "run {\n"
    for c in cmds:
        script += f"  {c}\n"
    script += "}\n"

    return script


def log_indicates_success(log_path):
    if not os.path.exists(log_path):
        return False

    try:
        with open(log_path, "r") as f:
            content = f.read()
    except Exception:
        return False

    if "Finished backup at" in content:
        return True

    if "Finished validate at" in content:
        return True

    return False


def main():
    module = AnsibleModule(
        argument_spec=dict(
            oracle_home=dict(type='str', required=True),
            oracle_sid=dict(type='str', required=True),
            rman_target=dict(type='str', required=False, default="/"),
            rman_catalog=dict(type='str', required=False, default=None),
            backup_type=dict(type='str', required=False, default='full',
                             choices=['full', 'incr']),
            level=dict(type='int', required=False),
            backup_dir=dict(type='str', required=True),
            tag=dict(type='str', required=False, default=None),
            validate_only=dict(type='bool', required=False, default=False),
            force=dict(type='bool', required=False, default=False),
        ),
        supports_check_mode=True
    )

    oracle_home = module.params['oracle_home']
    oracle_sid = module.params['oracle_sid']
    rman_target = module.params['rman_target']
    rman_catalog = module.params['rman_catalog']
    backup_type = module.params['backup_type']
    level = module.params['level']
    backup_dir = module.params['backup_dir']
    tag = module.params['tag']
    validate_only = module.params['validate_only']
    force = module.params['force']

    if not os.path.isdir(backup_dir):
        module.fail_json(msg=f"backup_dir {backup_dir} não existe ou não é diretório")

    now = datetime.datetime.now()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")

    if not tag:
        tag = f"ANSIBLE_{backup_type.upper()}_{date_str}"

    log_file = os.path.join(backup_dir, f"rman_{oracle_sid}_{date_str}_{time_str}.log")

    # Idempotência simples: se já existe log “ok” hoje com essa TAG, não roda de novo
    existing_logs = [
        os.path.join(backup_dir, f)
        for f in os.listdir(backup_dir)
        if re.match(rf"rman_{oracle_sid}_{date_str}_.*\.log", f)
    ]

    for lf in existing_logs:
        if log_indicates_success(lf):
            # Se não queremos forçar, considera que não há mudança
            if not force:
                module.exit_json(
                    changed=False,
                    msg=f"Backup já executado com sucesso hoje para {oracle_sid}.",
                    log_file=lf
                )

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Backup seria executado (check_mode).",
            log_file=log_file
        )

    try:
        rman_script = build_rman_script(backup_type, level, tag, validate_only)
    except ValueError as e:
        module.fail_json(msg=str(e))

    env = os.environ.copy()
    env['ORACLE_HOME'] = oracle_home
    env['ORACLE_SID'] = oracle_sid
    env['PATH'] = f"{oracle_home}/bin:" + env.get('PATH', '')

    cmd = ["rman", "target", rman_target]
    if rman_catalog:
        cmd.extend(["catalog", rman_catalog])

    try:
        proc = subprocess.run(
            cmd,
            input=rman_script,
            text=True,
            capture_output=True,
            env=env
        )
    except FileNotFoundError:
        module.fail_json(msg="Comando 'rman' não encontrado no PATH.", log_file=None)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    try:
        with open(log_file, "w") as f:
            f.write(stdout)
            if stderr:
                f.write("\n--- STDERR ---\n")
                f.write(stderr)
    except Exception as e:
        module.fail_json(msg=f"Falha ao escrever log: {e}", log_file=log_file)

    if proc.returncode != 0:
        module.fail_json(
            msg=f"RMAN retornou código {proc.returncode}.",
            log_file=log_file,
            stdout=stdout[-4000:],
            stderr=stderr[-4000:]
        )

    if "RMAN-" in stderr and "RMAN-00569" in stderr:
        module.fail_json(
            msg="RMAN reportou erro.",
            log_file=log_file,
            stdout=stdout[-4000:],
            stderr=stderr[-4000:]
        )

    module.exit_json(
        changed=True,
        msg="Backup RMAN executado com sucesso.",
        log_file=log_file,
        stdout=stdout[-4000:]
    )


if __name__ == '__main__':
    main()

#!/bin/zsh
# preferably script should be executed with sops exec-env ${enc_env_file} './script args'

# Read script arguments and options
local flag_help
local flag_dry
local flag_logs
local flag_copylog
local arg_build=(skip)  # default is to skip building new image
local flag_rebuild
local usage=(
  "## USAGE\n"
  "[-h|--help]\t\tshow this message"
  "[-d|--dry-run]\t\tdry run script with outputs"
  "[-l]--show-logs\t\tshow docker logs"
  "[-c]--copy-log\t\tcopy docker log file to local"
  "[-s]--skip-build\trestart container (for ex. in case of new env vars)"
  "[-b|--build]\t\tforce build new image with bumping tag [major, minor, patch]"
  "            \t\tskip building new docker image if missing or not specified"
  "[-r|--rebuild]\t\tforce rebuild image"
)

zparseopts -D -F -K -- \
  {h,\?,-help}=flag_help \
  {d,-dry-run}=flag_dry \
  {l,-show-logs}=flag_logs \
  {c,-copy-log}=flag_copylog \
  {s,-skip-build}=flag_skip \
  {b,-build}:=arg_build \
  {r,-rebuild}=flag_rebuild ||
  return

[[ "${flag_help}" ]] && print -l "${usage[@]}" && return

if [[ ${flag_skip} ]]; then
  bump_arg=${arg_build[-1]}
elif [[ ${arg_build[-1]} =~ (major|minor|patch|skip) ]]; then
  bump_arg=${arg_build[-1]}
else
  echo "## WARN: Unknown Semver Bumb arg '${arg_build[-1]}', using 'patch'"; bump_arg="patch"
fi

stop() {
  # exits script and return exit code (for testing)
  echo "## LOG: early script exit"
  exit "${1-0}"
}

check_utils() {
  # Sets "git root"
  git config --list | grep alias.root &> /dev/null
  if [[ $? != 0 ]]; then
    git config --global alias.root "rev-parse --show-toplevel"
  else
    [[ "${flag_dry}" ]] && echo -e "## DRY: git root alias is set"
  fi
  # csvquote util (https://github.com/dbro/csvquote) is needed
  #  in case of OSX installs it if not present or exits otherwise
  if ! command -v csvquote &> /dev/null; then
    if [[ $OSTYPE == darwin* ]]; then
      echo -e "installing csvquote"
      brew install sschlesier/csvutils/csvquote
    else
      echo -e "csvquote needed. It can be found at https://github.com/dbro/csvquote"
      stop 1
    fi
  else
    [[ "${flag_dry}" ]] && echo -e "## DRY: csvquote util is installed"
  fi
  # ssconvert util is needed
  #  in case of OSX installs it if not present or exits otherwise
  if ! command -v ssconvert &> /dev/null; then
		if [[ $OSTYPE == darwin* ]]; then
      echo -e "installing ssconvert"
      brew install gnumeric
    else
      echo -e "ssconvert not found. It can be found in gnumeric util"
      stop 1
    fi
  else
    [[ "${flag_dry}" ]] && echo -e "## DRY: ssconvert util is installed"
	fi
	# semver util (https://github.com/fsaintjacques/semver-tool) is needed
	#  in case of OSX installs it if not present or exits otherwise
  if ! command -v semver &> /dev/null; then
    if [[ $OSTYPE == darwin* ]]; then
      echo -e "installing semver"
      wget -O /usr/local/bin/semver https://raw.githubusercontent.com/fsaintjacques/semver-tool/master/src/semver &> /dev/null
      chmod +x /usr/local/bin/semver
      semver --version
    else
      echo -e "semver needed. It can be found at https://github.com/fsaintjacques/semver-tool"
      stop 1
    fi
  else
    [[ "${flag_dry}" ]] && echo -e "## DRY: semver util is installed"
  fi
}

compare_ids() {
  # compare ids
  old_array=(${(@s:,:)ALLOWED_TELEGRAM_USER_IDS})
  new_array=(${(@s:,:)ids})
  echo -e "## INFO: Users diff\n==================\nUsers added:"
  comm -13 <(echo $old_array | sort | tr ' ' '\n') <(echo $new_array | sort | tr ' ' '\n')
  echo -e "------------------\nUsers removed:"
  comm -23 <(echo $old_array | sort | tr ' ' '\n') <(echo $new_array | sort | tr ' ' '\n')
  echo -e "=================="
}

update_users() {
  echo -n "## INFO: Updating users list from yandex docs\n"
  users="${work_dir}/users.csv"
  base_url="https://cloud-api.yandex.net/v1/disk/resources/download"
  ya_disk_file="TEST_ChatGPTBot_Users.xlsx"
  ya_disk_path="/Service/${ya_disk_file}"
  _path=$(echo -n ${ya_disk_path} | jq -sRr @uri)
  req_url="${base_url}?path=${_path}"
  c_header="Accept: application/json"
  a_header="Authorization: OAuth ${YA_TOKEN}"
  download_url=$(curl -sX GET --header ${c_header} --header ${a_header} ${req_url} | jq -r '.href')
  curl -sL "${download_url}" -o "${users}"
  ssconvert "${users}" "${users}"
  column=$(csvheader "${users}" | grep telegram | awk '{print $1}')
  ids=$(csvquote ${users} | tr -d '\r' | awk -v col="${column}" -F, '(NR>1 && length($col)>0){print $col}' | sort | uniq | paste -sd "," -)
  compare_ids
  rm -f "${users}"
  if [[ "${flag_dry}" ]]; then
      return
  fi
  echo "update env"
#  sed -i '' "/^ALLOWED_TELEGRAM_USER_IDS=/s/=.*/=${ids}/" .env; source_env
}

set_envs() {
  # set variables
  env_file=".env"
  # set docker context, image name and current image version
  context='cit-droplet'
  name=${PWD##*/}

  old_name='sputnik_bot'
  if [[ -z "${old_name}" ]]; then
    old_name=$name
  fi

  IMAGE_ID=$(docker --context=${context} images ${old_name}:latest --format "{{.ID}}")
  if [ -z "${IMAGE_ID}" ]; then
    cur_tag=0.0.0
  else
    IMAGE_NAME=$(docker --context=${context} image inspect "${IMAGE_ID}" | jq -r '.[].RepoTags[] | select( test("latest")|not )')
    cur_tag=${IMAGE_NAME##*:}
  fi
  if [[ -v YA_TOKEN ]]; then
    update_users
  fi
}

docker_build() {
  if [[ "${1}" == "skip" && ! "${flag_dry}" && ! "${flag_rebuild}" ]]; then
    echo -n "## INFO: Skip building docker image\n"
    return
  fi
  if [[ "${flag_rebuild}" ]]; then
    echo -e "## INFO: Rebuilding docker image with the same tag (${1})"
  else
      echo -e "## INFO: Bumping docker tag version (${1})"
  fi
  if [[ "${flag_dry}" || "${flag_rebuild}" ]]; then
    new_tag=${cur_tag}
  elif [[ ${cur_tag} == '0.0.0' ]]; then
    new_tag=$(semver bump minor ${cur_tag})
  else
    new_tag=$(semver bump ${1} ${cur_tag})
  fi
  echo -e "${cur_tag} --> ${new_tag}"
  echo -e "## INFO: Building new docker image ${name}:${new_tag}"
  if [[ "${flag_dry}" ]]; then
    echo -e "## DRY: docker --context=${context} build --no-cache -t ${name}:${new_tag} -t ${name} ."
    return
  fi
  docker --context=${context} build --pull --no-cache -t ${name}:${new_tag} -t ${name} .
}

docker_copy_logs() {
  local_log_file="${name}_docker.log"
  remote_log_file=$(docker --context ${context} inspect --format='{{.LogPath}}' ${name})
  echo -e "## INFO: ${name} docker logs appended to ${local_log_file}"
  rsync -avzh --progress --rsync-path="sudo rsync" ${context}:${remote_log_file} tmp.log
  jq -r .log < tmp.log | sed '/^\s*$/d' >> ${local_log_file}
  rm tmp.log
  echo
}

docker_run() {
  if [[ "${flag_dry}" ]]; then
    echo -e "## DRY: Restarting docker container with new ${env_file}"
    return
  fi
  if [[ ! "${flag_logs}" ]]; then
    echo -e "## INFO: removing old container"
    docker_copy_logs
    docker --context ${context} rm -f ${old_name}
    echo -e "## INFO: starting new container"
    docker --context ${context} run -d -v /home/aamite/${name}_usage_logs:/app/usage_logs -v /home/aamite/${name}_data:/app/data --env-file ${env_file} --restart=always --name=${name} ${name}
    echo -e "## INFO: docker processes status"
    docker --context ${context} ps -a
  fi
  echo -e "## INFO: docker container log"
  docker --context ${context} logs ${name}
  echo
  if [[ "${flag_copylog}" ]]; then
    docker_copy_logs
  fi
}

work_dir=$(git root)
check_utils
set_envs
docker_build ${bump_arg}
docker_run
#stop

##################
#context='cit-droplet'
#name='mia_rs_chat_bot'
#new_tag=$(semver bump minor 0.0.0)
#docker --context=${context} build --no-cache -t ${name}:${new_tag} -t ${name} .
#docker --context ${context} run -d -v /home/aamite/${name}_usage_logs:/app/usage_logs -v /home/aamite/${name}_data:/app/data --env-file ./.env --restart=always --name=${name} ${name}

README
=

## Prerequisites
1. [git-flow (AVH Edition)][1]
2. [git-flow-hooks][2]
   1. clone repo and symlink it to .git/hooks folder
      ```shell
      mkdir -p .git/hooks/samples
      cp .git/hooks/*.sample .git/hooks/samples/
      ln -s "fullpath_to_hooks_repo/*" .git/hooks
      ```
3. [sops utility][3]
4. [age encryption][4]
5. custom git clean filter (credits to [**this**][5])<br>
   Appropriate filters are already specified in [git attributes](.gitattributes)<br> 
   ```shell
   file='.git/hooks/git_filter_sops_clean'
   cat << "EOF" > "${file}"
   #!/usr/bin/env -S zsh -euo pipefail
   # we need $1 to be the path of the file so we can check the previous version
   # via git-show to prevent the encryption's non-determinism from resulting in
   # unnecessary changes
   if test $# -ne 1; then
     echo "Usage: $0 FILE" >&2
     exit 1
   fi
   
   if ! git cat-file -e "HEAD:$1" &>/dev/null; then
     # if git cat-file -e fails, then the file doesn't exist at HEAD,
     # so it's new,  meaning we need to encrypt it for the first time
     echo "$0: No previous version found while cleaning '$1'" >&2
     sops --input-type dotenv --output-type dotenv --encrypt $1
   elif diff <(git cat-file --filters "HEAD:$1") <(cat $1) >/dev/null; then
     echo "$0: Previous version of '$1' found. Comparing..." >&2
     # if there's no difference between the decrypted version of the file at HEAD
     # and the new contents, then we re-use the previous version to prevent
     # unnecessary file updates
     echo "$0: No changes found while cleaning '$1'" >&2
     git cat-file -p "HEAD:$1"
   else
     # if there is a difference then we re-encrypt it $1
     echo "$0: Changes found while cleaning '$1'" >&2
     sops --input-type dotenv --output-type dotenv --encrypt $1
   fi
   EOF
   chmod +x "${file}"
   git config --local filter.sops_dotenv.clean "$(realpath ${file}) %f"
   git config --local filter.sops_dotenv.smudge "sops --input-type dotenv --output-type dotenv --decrypt /dev/stdin"
   git config --local filter.sops_dotenv.required true
   ```
-----------
[1]: https://github.com/petervanderdoes/gitflow-avh
[2]: https://github.com/jaspernbrouwer/git-flow-hooks
[3]: https://github.com/getsops/sops
[4]: https://github.com/FiloSottile/age
[5]: https://github.com/getsops/sops/issues/1137

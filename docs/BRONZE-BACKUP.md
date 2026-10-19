# Bronze 보관과 복구

Bronze를 Actions cache에만 두지 않는다. `scripts/bronze_backup.py`는 수집이 끝난 시점의 파일들을
SHA-256 객체 저장소와 불변 snapshot manifest로 복사한다. 같은 내용은 중복 저장하지 않는다.
스냅샷에는 수집 원본·매니페스트·벤더 버전/시각 로그가 그대로 포함된다.
저장소와 Bronze는 서로 포함하지 않는 별도 경로여야 하며 원천을 덮어쓰거나 자동 삭제하지 않는다.

실행한 로컬 저장소: `C:/Users/FELAB/Desktop/projects/backups/smic-easy-bronze`.
초기 전체 2,776파일, 4,486,110,048바이트를 검증하고 빈 폴더 전체 복원까지 확인했다.
이후 추가된 모의 포트폴리오 관측은 후속 스냅샷에 포함한다.
이 경로는 CI 캐시와 독립적이지만 같은 물리 디스크다. 디스크 장애 대비 원격 보관은 별도 설정해야 한다.

```powershell
uv run python scripts/bronze_backup.py create --store C:/Users/FELAB/Desktop/projects/backups/smic-easy-bronze
uv run python scripts/bronze_backup.py verify --store STORE --manifest STORE/snapshots/HASH.json
uv run python scripts/bronze_backup.py restore --store STORE --manifest STORE/snapshots/HASH.json --destination EMPTY/bronze
uv run python scripts/bronze_backup.py restore-latest --store STORE --destination EMPTY/bronze
```

manifest·객체 해시·크기, 중복·경로 이탈·심볼릭 링크·기존 파일 충돌을 모두 사전 검사한다.
복원은 동일한 기존 파일을 건너뛰고 다른 내용이면 전체 쓰기 전에 실패한다.
캐시를 잃은 기기에서는 빈 디렉터리로 복구해 검증 후 프로젝트 경로로 사용한다.
`restore-latest`는 전체 초기 스냅샷과 후속 delta의 파일별 최신 버전을 합성한다.

## 선택적 S3 영구 보관

최초 전체 저장소를 원격에 업로드한 뒤, 두 refresh 워크플로가 수집 당시 존재하는 원천 delta를 추가한다.
GitHub checkout에는 과거 PDF가 없으므로 최초 전체 업로드 없이 CI delta만으로 전체 과거 원천이 보장되지 않는다.

- Actions Variables: `BRONZE_BACKUP_S3_URI` (`s3://BUCKET/PREFIX/`), `BRONZE_AWS_REGION`.
- S3 호환 서비스의 경우 선택적 `BRONZE_AWS_ENDPOINT_URL`도 지정한다.
- Actions Secrets: `BRONZE_AWS_ACCESS_KEY_ID`, `BRONZE_AWS_SECRET_ACCESS_KEY`.
- 필요한 권한은 해당 prefix의 List/Get/Put으로 제한하고 Delete는 부여하지 않는다.
- 버킷은 비공개로 유지하고 버전 관리와 장기 보존 정책을 별도로 설정한다. 자동 만료·prune는 구현하지 않는다.

```powershell
# 검증된 전체 로컬 저장소의 초기 원격 복사. 자격증명은 AWS 표준 설정 사용.
aws s3 sync LOCAL_STORE/objects/ s3://BUCKET/PREFIX/objects/ --only-show-errors --no-follow-symlinks
aws s3 sync LOCAL_STORE/snapshots/ s3://BUCKET/PREFIX/snapshots/ --only-show-errors --no-follow-symlinks
# 새 기기: 원격 저장소를 내려받고 전체 manifest·객체 검증 후 최신 복원.
aws s3 sync s3://BUCKET/PREFIX/ LOCAL_STORE/ --only-show-errors --no-follow-symlinks
uv run python scripts/bronze_backup.py restore-latest --store LOCAL_STORE --destination EMPTY/bronze
```

`--delete`를 사용하지 않는다. S3 호환 서비스는 AWS CLI의 표준 endpoint 설정을 사용한다.
외부 저장소 미설정 상태에서는 업로드 단계를 실행하지 않는다. 현재 외부 업로드·재해 복구 검증은 미실행이다.
CLI 의미: [AWS s3 sync](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html).

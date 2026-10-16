---
name: gcloud
description: Conventions for this machine's GKE kube contexts (incl. Connect Gateway for gluru-* clusters), Cloud Logging filter recipes, and gcloud reauth handling. Use before running kubectl or `gcloud logging`/`auth` commands.
---

# gcloud / kubectl

## GKE
Always pass `kubectl --context <ctx>`; never switch the global context. List with `kubectl config get-contexts -o name`.
- gluru-* (private; Connect Gateway): `connectgateway_<project>_<location>_<cluster>`. If missing:
  `gcloud container fleet memberships get-credentials <cluster> --location <location> --project <project>`
- Others: `<project>-<cluster>-<location>`. If missing:
  ```bash
  gcloud container clusters get-credentials "$CLUSTER" --location "$LOCATION" --project "$PROJECT"
  kubectl config rename-context "gke_${PROJECT}_${LOCATION}_${CLUSTER}" "${PROJECT}-${CLUSTER}-${LOCATION}"
  ```

## Cloud Logging
`gcloud logging read 'FILTER' --project=P --freshness=DUR --limit=N --format=json`. Always bound by time and limit.
- `resource.type="k8s_container" resource.labels.cluster_name="C" resource.labels.namespace_name="NS"`
- `resource.type="cloud_run_revision" resource.labels.service_name="S"`
- `severity>=ERROR`

## Auth
Check: `gcloud auth list; gcloud config get-value project`. On "Reauthentication failed", stop and ask the user to run `gcloud auth login`.

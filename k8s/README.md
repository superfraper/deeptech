Kubernetes deployment for ESF Dash RAG

Prereqs
- NGINX Ingress Controller installed in the cluster
- DNS pointing to the Ingress Load Balancer
- cert-manager installed (recommended) and a TLS secret created as `esf-tls` or adjust manifests

Manifests
- `namespace.yaml`: Namespace `esf`
- `secrets.yaml`: App secrets (fill in values or replace with ExternalSecrets/IRSA)
- `configmap.yaml`: Non-secret configuration
- `pvc-backend.yaml`: Persistent storage for SQLite databases
- `backend-deployment.yaml`: Backend Deployment and Service
- `frontend-deployment.yaml`: Frontend Deployment and Service
- `ingress-backend-upload.yaml`: Dedicated Ingress for `/upload` with buffering off and larger body size
- `ingress-backend-api.yaml`: Ingress for other API routes
- `ingress-frontend.yaml`: Ingress routing for SPA

Steps
1) Build and push images
   - backend -> your-registry/esf-backend:latest
   - frontend -> your-registry/esf-frontend:latest
   Update image references in the Deployment manifests.

2) Apply namespace and configs
   kubectl apply -f k8s/namespace.yaml
   kubectl apply -f k8s/secrets.yaml
   kubectl apply -f k8s/configmap.yaml
   kubectl apply -f k8s/pvc-backend.yaml

3) Deploy services
   kubectl apply -f k8s/backend-deployment.yaml
   kubectl apply -f k8s/frontend-deployment.yaml

4) Create Ingresses
   Replace `app.example.com` with your hostname in all Ingresses.
   kubectl apply -f k8s/ingress-backend-upload.yaml
   kubectl apply -f k8s/ingress-backend-api.yaml
   kubectl apply -f k8s/ingress-frontend.yaml

5) Configure frontend base URL
   Set `REACT_APP_API_URL` to the same origin base (e.g., `https://app.example.com`).
   The SPA fetches API via absolute paths, routed by Ingress.

Notes
- `/upload` is isolated to disable buffering and increase `proxy-body-size`, fixing 502s seen with rate limits/buffering.
- Backend replicas are 1 due to SQLite. Migrate to an external DB before scaling.
- Consider IRSA for AWS access to avoid static keys in Secrets on EKS.


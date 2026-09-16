pipeline {
    agent none

    environment {
        REGISTRY = 'registry.idsnetwork.org'
        PROJECT  = 'iris'
        DEPLOY_ENV = "${deployEnv() ?: ''}"
    }

    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '30'))
        disableConcurrentBuilds()
    }

    tools {
        go 'go1.26'
    }

    stages {
        stage('CI') {
            agent { label 'docker' }

            environment {
                DOCKER_CONFIG = "${WORKSPACE_TMP}/docker"
                GOLANGCI_LINT_CACHE = "/var/cache/jenkins/golangci/${JOB_BASE_NAME}"
            }

            stages {

                stage('Lint') {
                    steps { sh 'make lint' }
                }

                stage('Test') {
                    steps { sh 'make test' }
                    post {
                        always {
                            junit testResults: 'reports/*.xml', allowEmptyResults: true
                        }
                    }
                }

                stage('Build') {
                    steps {
                        script {
                            sh "mkdir -p ${env.DOCKER_CONFIG}"
                            env.GIT_SHA   = env.GIT_COMMIT.take(12)
                            env.IMAGE_REF = buildAndPush(env.REGISTRY, env.PROJECT, env.GIT_SHA)
                            echo "built ${env.IMAGE_REF}"
                            currentBuild.description = "${env.DEPLOY_ENV ?: 'ci'} ${env.GIT_SHA}"
                        }
                    }
                }

                stage('Verify push') {
                    steps {
                        script {
                            docker.withRegistry("https://${env.REGISTRY}", 'registry-creds') {
                                // Remove EVERY local reference to the image, not just the
                                // digest one. The tags share an image ID, so removing the
                                // digest alone leaves the layers cached and makes the
                                // pull below a no-op that would pass on an empty registry.
                                sh """
                                    docker image rm \
                                        ${env.REGISTRY}/${env.PROJECT}:${env.GIT_SHA} \
                                        ${env.REGISTRY}/${env.PROJECT}:cache \
                                        ${env.IMAGE_REF} || true
                                """
                                sh "docker pull ${env.IMAGE_REF}"
                            }
                            echo "verified ${env.IMAGE_REF}"
                        }
                    }
                }
            }

            post {
                always {
                    sh 'docker logout "$REGISTRY" || true'
                    // Verify push pulls the image back by digest, so both the
                    // tag and the digest reference can be left on the agent.
                    sh(script: 'docker image rm "$REGISTRY/$PROJECT:$GIT_SHA" "$IMAGE_REF" || true',
                       returnStatus: true)
                    cleanWs()
                }
            }
        }

        stage('Approve production') {
            when {
                beforeInput true
                expression { env.DEPLOY_ENV == 'production' }
            }
            options { timeout(time: 1, unit: 'HOURS') }
            input {
                message "Deploy ${env.PROJECT} to production?"
                ok 'Deploy'
                submitter 'release-managers'
                submitterParameter 'APPROVER'
            }
            steps {
                echo "production deploy approved by ${env.APPROVER}"
            }
        }

        stage('Deploy') {
            agent { label 'docker' }
            when {
                beforeAgent true
                expression { env.DEPLOY_ENV }
            }
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                script {
                    // A null IMAGE_REF interpolates as the literal string "null",
                    // which make would happily pass along. Fail loudly instead.
                    // Also catches "Restart from Stage", which loses build env vars.
                    if (!env.IMAGE_REF) {
                        error 'IMAGE_REF is not set - the Build stage did not produce an image'
                    }

                    def cfg = environments()[env.DEPLOY_ENV]
                    if (!cfg) { error "no environment config for '${env.DEPLOY_ENV}'" }

                    lock(resource: "deploy-${env.DEPLOY_ENV}-${env.PROJECT}") {
                        // cfg.host already carries the user (deploy@...), so no
                        // usernameVariable here - one source of truth.
                        withCredentials([sshUserPrivateKey(
                                credentialsId: cfg.credId,
                                keyFileVariable: 'SSH_KEY')]) {
                            sh """
                                make deploy \
                                    IMAGE=${env.IMAGE_REF} \
                                    HOST=${cfg.host} \
                                    ENVIRONMENT=${env.DEPLOY_ENV}
                            """
                        }
                    }
                    echo "deployed ${env.PROJECT} -> ${env.DEPLOY_ENV} (${env.IMAGE_REF})"
                }
            }
            post { always { cleanWs() } }
        }
    }
}

// ---- no script-level variables below this line, only methods ----

def environments() {
    [ staging:    [host: 'deploy@iris-dev.idsnetwork.org', credId: 'deploy-dev-ssh'],
      production: [host: 'deploy@iris.idsnetwork.org',    credId: 'deploy-prod-ssh'] ]
}

def deployEnv() {
    switch (env.BRANCH_NAME) {
        case 'main': return 'production'
        case 'dev':  return 'staging'
        default:     return null
    }
}

def buildAndPush(String registry, String project, String sha) {
    def repo = "${registry}/${project}"
    def tag  = "${repo}:${sha}"

    docker.withRegistry("https://${registry}", 'registry-creds') {
        // No `| tee`. sh runs without pipefail, so a pipeline's exit status is
        // tee's, and a failed push would pass silently.
        sh """
            DOCKER_BUILDKIT=1 docker build \
                --build-arg BUILDKIT_INLINE_CACHE=1 \
                --cache-from ${repo}:cache \
                -f Dockerfile \
                -t ${tag} -t ${repo}:cache \
                .
            docker push ${tag} > push.log
            cat push.log
            docker push ${repo}:cache
        """
    }

    // `|| true` matters: sh runs with -e, so a grep that matches nothing exits 1
    // and fails the step with a bare exit code, making the error below dead code.
    def digest = sh(script: "grep -o 'sha256:[0-9a-f]\\{64\\}' push.log | tail -1 || true",
                    returnStdout: true).trim()
    if (!digest) { error "could not determine pushed digest for ${tag}" }
    return "${repo}@${digest}"
}

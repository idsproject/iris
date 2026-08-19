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

    stages {
        stage('CI') {
            agent { label 'docker' }

            environment {
                DOCKER_CONFIG = "${WORKSPACE_TMP}/docker-${BRANCH_NAME}"
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
                                sh "docker image rm ${env.IMAGE_REF} || true"
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
                    sh(script: 'docker image rm "$REGISTRY/$PROJECT:$GIT_SHA" || true',
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
                message "Deploy {$env.PROJECT} to production?"
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
            steps {
                script {
                    def cfg = environments()[env.DEPLOY_ENV]

                    lock(resource: "deploy-${env.DEPLOY_ENV}-${env.PROJECT}") {
                        withCredentials([sshUserPrivateKey(
                                credentialsId: cfg.credId,
                                keyFileVariable: 'SSH_KEY',
                                usernameVariable: 'SSH_USER')]) {
                            sh """
                                make deploy \
                                    IMAGE=${env.IMAGE_REF} \
                                    HOST=${cfg.host} \
                                    ENVIRONMENT=${env.DEPLOY_ENV}
                            """
                        }
                    }
                    echo "deployed ${env.PROJECT} → ${env.DEPLOY_ENV} (${env.IMAGE_REF})"
                }
            }
            post { always { cleanWs() } }
        }
    }
}

// ---- no script-level variables below this line, only methods ----

def environments() {
    [ staging:    [host: 'deploy@iris-dev.idsnetwork.org', credId: 'deploy-staging'],
      production: [host: 'deploy@iris.idsnetwork.org',    credId: 'deploy-prod'] ]
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
        sh """
            DOCKER_BUILDKIT=1 docker build \
                --build-arg BUILDKIT_INLINE_CACHE=1 \
                --cache-from ${repo}:cache \
                -f Dockerfile \
                -t ${tag} -t ${repo}:cache \
                .
            docker push ${tag} | tee push.log
            docker push ${repo}:cache
        """
    }

    def digest = sh(script: "grep -o 'sha256:[0-9a-f]\\{64\\}' push.log | tail -1",
                    returnStdout: true).trim()
    if (!digest) { error "could not determine pushed digest for ${tag}" }
    return "${repo}@${digest}"
}


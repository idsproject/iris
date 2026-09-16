package aws

import (
	"context"
	"errors"
	"io"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/feature/s3/manager"
	tm "github.com/aws/aws-sdk-go-v2/feature/s3/transfermanager"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	// "github.com/aws/aws-sdk-go-v2/service/s3/types"
)

const partMiBs int64 = 5
const thresholdMiBs int64 = 10

var ErrBadPath = errors.New("bad file path")
var ErrLoadConfig = errors.New("error loading AWS SDK config")
var ErrCallS3 = errors.New("error calling S3")

type AwsS3Connection struct {
	ctx       context.Context
	s3Client  *s3.Client
	tManager  *tm.Client
	awsConfig aws.Config
}

type BucketObject struct {
	Key  string
	Size int64
}

func NewAwsS3Connection(ctx context.Context) (*AwsS3Connection, error) {
	sdkConfig, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		return nil, errors.Join(ErrLoadConfig, err)
	}

	s3Client := s3.NewFromConfig(sdkConfig)

	tManClient := tm.New(s3Client, func(opts *tm.Options) {
		opts.PartSizeBytes = partMiBs * 1024 * 1024
		opts.MultipartUploadThreshold = thresholdMiBs * 1024 * 1024
	})

	return &AwsS3Connection{
		ctx:       ctx,
		awsConfig: sdkConfig,
		s3Client:  s3Client,
		tManager:  tManClient,
	}, nil
}

func (conn *AwsS3Connection) getBuckets() ([]string, error) {
	result, err := conn.s3Client.ListBuckets(conn.ctx, &s3.ListBucketsInput{})
	if err != nil {
		return nil, errors.Join(ErrCallS3, err)
	}

	buckets := make([]string, 0)
	for _, bucket := range result.Buckets {
		buckets = append(buckets, *bucket.Name)
	}

	return buckets, nil
}

func (conn *AwsS3Connection) uploadFile(file io.Reader, bucketName, objectKey string, fileSize *int64) error {
	input := &tm.UploadObjectInput{
		Bucket: aws.String(bucketName),
		Key:    aws.String(objectKey),
		Body:   file,
	}
	if fileSize != nil {
		input.ContentLength = fileSize
	}

	_, err := conn.tManager.UploadObject(conn.ctx, input)
	if err != nil {
		return err
	}

	err = s3.NewObjectExistsWaiter(conn.s3Client).Wait(
		conn.ctx,
		&s3.HeadObjectInput{Bucket: aws.String(bucketName), Key: aws.String(objectKey)},
		time.Minute)
	if err != nil {
		return err
	}

	return nil
}

func (conn *AwsS3Connection) listObjects(bucketName string) ([]BucketObject, error) {
	var objects []BucketObject

	objectPaginator := s3.NewListObjectsV2Paginator(conn.s3Client, &s3.ListObjectsV2Input{
		Bucket: aws.String(bucketName),
	})
	for objectPaginator.HasMorePages() {
		output, err := objectPaginator.NextPage(conn.ctx)
		if err != nil {
			return nil, err
		}
		for _, object := range output.Contents {
			objects = append(objects, BucketObject{
				Key:  *object.Key,
				Size: *object.Size,
			})
		}
	}
	return objects, nil
}

func (conn *AwsS3Connection) downloadFile(bucketName, objectKey string) ([]byte, error) {
	buffer := manager.NewWriteAtBuffer([]byte{})
	_, err := conn.tManager.DownloadObject(conn.ctx, &tm.DownloadObjectInput{
		Bucket:   aws.String(bucketName),
		Key:      aws.String(objectKey),
		WriterAt: buffer,
	})
	if err != nil {
		return nil, err
	}

	return buffer.Bytes(), nil
}

func (conn *AwsS3Connection) deleteFile(bucketName, objectKey, versionId string, bypassGovernance bool) error {
	input := &s3.DeleteObjectInput{
		Bucket: aws.String(bucketName),
		Key:    aws.String(objectKey),
	}
	if versionId != "" {
		input.VersionId = aws.String(versionId)
	}
	if bypassGovernance {
		input.BypassGovernanceRetention = aws.Bool(true)
	}

	_, err := conn.s3Client.DeleteObject(conn.ctx, input)
	if err != nil {
		return err
	}

	err = s3.NewObjectNotExistsWaiter(conn.s3Client).Wait(
		conn.ctx,
		&s3.HeadObjectInput{
			Bucket: aws.String(bucketName),
			Key:    aws.String(objectKey),
		},
		time.Minute)
	if err != nil {
		return err
	}

	return nil
}

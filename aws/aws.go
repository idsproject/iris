package aws

import (
	"context"
	"errors"
	"log/slog"
	"os"
	"time"
    "bytes"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
    "github.com/aws/aws-sdk-go-v2/feature/s3/manager"
    "github.com/aws/aws-sdk-go-v2/service/s3/types"
)

const partMiBs int64 = 10

var ErrLoadConfig = errors.New("error loading AWS SDK config")
var ErrCallS3 = errors.New("error calling S3")

type AwsS3Connection struct {
	ctx       context.Context
	awsConfig aws.Config
	s3Client  *s3.Client
}

type BucketObject struct {
	Key  string
	Size int64
}

func NewAwsS3Connection(ctx context.Context) (*AwsS3Connection, error) {
	if ctx == nil {
		ctx = context.WithValue(context.Background(), slog.New(slog.NewTextHandler(os.Stdout, nil)), "logger")
	}

	sdkConfig, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		return nil, errors.Join(ErrLoadConfig, err)
	}

	client := s3.NewFromConfig(sdkConfig)

	return &AwsS3Connection{
		ctx:       ctx,
		awsConfig: sdkConfig,
		s3Client:  client,
	}, nil
}

func (conn *AwsS3Connection) GetBuckets() ([]string, error) {
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

func (conn *AwsS3Connection) UploadFile(file *os.File, bucketName, objectKey string) error {
	_, err := conn.s3Client.PutObject(conn.ctx, &s3.PutObjectInput{
		Bucket: aws.String(bucketName),
		Key:    aws.String(objectKey),
		Body:   file,
	})
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

func (conn *AwsS3Connection) UploadLargeFile(localFilePath, bucketName, objectKey string) error {
	data, err := os.ReadFile(localFilePath)
	if err != nil {
		return err
	}

	largeBuffer := bytes.NewReader(data)
	uploader := manager.NewUploader(conn.s3Client, func(u *manager.Uploader) {
		u.PartSize = partMiBs * 1024 * 1024
	})
	_, err = uploader.Upload(conn.ctx, &s3.PutObjectInput{
		Bucket: aws.String(bucketName),
		Key:    aws.String(objectKey),
		Body:   largeBuffer,
	})
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

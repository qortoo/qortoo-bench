package main

import qortoo "github.com/qortoo/qortoo-go"

type benchClient struct {
	client *qortoo.Client
	conn   *qortoo.LocalConnectivity
}

func newClient() *benchClient {
	conn := qortoo.NewLocalConnectivity()
	conn.SetRealtime(false)
	client, err := qortoo.NewClient("bench-collection", "bench-counter", qortoo.WithLocalConnectivity(conn))
	if err != nil {
		conn.Close()
		panic(err)
	}
	// Historical SDK revisions used by candidate comparisons open in NewClient.
	if opener, ok := any(client).(interface{ Open() error }); ok {
		if err := opener.Open(); err != nil {
			client.Close()
			conn.Close()
			panic(err)
		}
	}
	return &benchClient{client: client, conn: conn}
}

func (bc *benchClient) close() {
	bc.client.Close()
	bc.conn.Close()
}

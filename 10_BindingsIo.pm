
# $Id: 10_BindingsIo.pm 18283 2019-01-16 16:58:23Z dominikkarall $

package main;

use strict;
use warnings;

use JSON;
use Time::HiRes qw(time);
use Protocol::WebSocket::Frame;

sub Log($$);
sub Log3($$$);

sub
BindingsIo_Initialize($)
{
  my ($hash) = @_;

  $hash->{parseParams} = 1;

  $hash->{DefFn}    = 'BindingsIo_Define';
  $hash->{UndefFn}  = 'BindingsIo_Undefine';
  $hash->{GetFn}    = 'BindingsIo_Get';
  $hash->{SetFn}    = 'BindingsIo_Set';
  $hash->{AttrFn}   = 'BindingsIo_Attr';

  $hash->{ReadFn}   = 'BindingsIo_Read';
  $hash->{ReadyFn}  = 'BindingsIo_Ready';
  $hash->{WriteFn}  = 'BindingsIo_Write';

  $hash->{Clients} = "PythonModule:NodeModule";

  return undef;
}

sub
BindingsIo_Define($$$)
{
  my ($hash, $a, $h) = @_;

  Log3 $hash, 3, "BindingsIo v1.0.0";

  my $bindingType = ucfirst(@$a[2]);

  # TODO handover port as parameter to binding server
  # - define of PythonServer needs to handle it
  # - fhem_pythonbridge needs to handle it
  my $port = 15732;
  if ($bindingType eq "Python") {
    $port = 15733;
  }
  $hash->{DeviceName} = "ws:127.0.0.1:".$port;

  my $foundServer = 0;
  foreach my $fhem_dev (sort keys %main::defs) {
    $foundServer = 1 if($main::defs{$fhem_dev}{TYPE} eq $bindingType."Server");
  }
  if ($foundServer == 0) {
    CommandDefine(undef, $bindingType."binding ".$bindingType."Binding ".$port);
    InternalTimer(gettimeofday()+3, "BindingsIo_connectDev", $hash, 0);
  } else {
    BindingsIo_connectDev($hash);
  }

  return undef;
}

sub
BindingsIo_connectDev($) {
  my ($hash) = @_;
  DevIo_CloseDev($hash);
  DevIo_OpenDev($hash, 0, undef, "BindingsIo_doInit");
}

sub
BindingsIo_doInit($) {
  my ($hash) = @_;

  # TODO initialize all devices (send Define)
  BindingsIo_connectionCheck($hash);
}

sub 
BindingsIo_connectionCheck($) {
  my ($hash) = @_;
  my %msg = (
    "msgtype" => "ping"
  );
  DevIo_SimpleWrite($hash, encode_json(\%msg), 0);
  my $response = DevIo_SimpleReadWithTimeout($hash, 1);
  my $frame = Protocol::WebSocket::Frame->new;
  $frame->append($response);
  $response = $frame->next;
  if ($response eq "") {
    RemoveInternalTimer($hash);
    DevIo_Disconnected($hash);
  } else {
    RemoveInternalTimer($hash);
    InternalTimer(gettimeofday()+10, "BindingsIo_connectionCheck", $hash, 0);
  }
}

sub
BindingsIo_Read($)
{
  my ($hash) = @_;
  my $name = $hash->{NAME};

  my $ret = BindingsIo_readWebsocketMessage($hash, undef);
  # TODO set alle modules to offline if $ret eq "offline"
}

sub
BindingsIo_Ready($)
{
  my ($hash) = @_;
  my $name = $hash->{NAME};

  return DevIo_OpenDev($hash, 1, undef, "BindingsIo_doInit") if($hash->{STATE} eq "disconnected");
}

sub
BindingsIo_Write($$$$) {
  my ($hash, $devhash, $function, $a, $h) = @_;

  if($hash->{STATE} eq "disconnected") {
    readingsSingleUpdate($devhash, "state", "PythonServer offline", 1);
    return undef;
  }

  Log3 $hash, 3, "callPythonFunction: ".$devhash->{NAME}." => ".$function;

  if ($function eq "Define") {
    $devhash->{args} = $a;
    $devhash->{argsh} = $h;
    $devhash->{PYTHONTYPE} = @$a[2];
  }

  my %msg = (
    "msgtype" => "function",
    "NAME" => $devhash->{NAME},
    "PYTHONTYPE" => $devhash->{PYTHONTYPE},
    "function" => $function,
    "args" => $a,
    "argsh" => $h,
    "defargs" => $devhash->{args},
    "defargsh" => $devhash->{argsh}
  );
  Log3 $hash, 3, "<<< WS: ".encode_json(\%msg);
  DevIo_SimpleWrite($hash, encode_json(\%msg), 0);

  my $returnval = "";
  my $t1 = time * 1000;
  while (1) {
    my $t2 = time * 1000;
    if (($t2 - $t1) > 1000) {
      # stop loop after ... ms
      Log3 $hash, 3, "Timeout while waiting for function to finish ($function)";
      $returnval = "Timeout while waiting for reply from $function";
      last;
    }
    
    $returnval = BindingsIo_readWebsocketMessage($hash, $devhash);
    if ($returnval ne "empty" && $returnval ne "continue") {
      last;
    }
  }

  if ($returnval eq "") {
    $returnval = undef;
  } elsif ($returnval eq "offline") {
    #readingsSingleUpdate($devhash, "state", "PythonServer offline", 1);
  }
  
  return $returnval;
}

sub
BindingsIo_Undefine($$)
{
  my ($hash, $name) = @_;

  RemoveInternalTimer($hash);
  DevIo_CloseDev($hash);

  return undef;
}

sub
BindingsIo_Get($$$)
{
  my ($hash, $a, $h) = @_;

  return undef;
}

sub
BindingsIo_Set($$$)
{
  my ($hash, $a, $h) = @_;

  return undef;
}

sub
BindingsIo_Attr($$$)
{
  my ($cmd, $name, $attrName, $attrVal) = @_;

  return undef;
}

sub
BindingsIo_DelayedShutdownFn($)
{
  my ($hash) = @_;

  DevIo_CloseDev($hash);

  return undef;
}

sub
BindingsIo_Shutdown($)
{
  my ($hash) = @_;

  DevIo_CloseDev($hash);

  return undef;
}

#### CALL NODE SUBS ####
sub BindingsIo_readWebsocketMessage($$) {
  my ($hash, $devhash) = @_;

  my $response = DevIo_SimpleReadWithTimeout($hash, 1);
  return "empty" if (!defined($response));

  my $frame = Protocol::WebSocket::Frame->new;
  $frame->append($response);
  while ($response = $frame->next) {
    Log3 $hash, 3, ">>> WS: ".$response;
    if ($response eq "") {
      DevIo_Disconnected($hash);
      return "offline";
    }
    my $json = eval {decode_json($response)};
    if ($@) {
      Log3 $hash, 3, "JSON error: ".$@;
      return "error";
    }

    my $returnval = "";
    if ($json->{msgtype} eq "function") {
      if ($json->{finished} == "1" && defined($devhash)) {
        if ($json->{error}) {
          return $json->{error};
        }
        if ($devhash->{NAME} ne $json->{NAME}) {
          Log3 $hash, 1, "Received wrong WS message, waiting for ".$devhash->{NAME}.", but received ".$json->{NAME};
          return "error";
        }
        foreach my $key (keys %$json) {
          next if ($key eq "msgtype" or $key eq "finished" or $key eq "ws" or $key eq "returnval" or $key 
            eq "function" or $key eq "defargs" or $key eq "defargsh" or $key eq "args" or $key eq "argsh");
          $devhash->{$key} = $json->{$key};
        }
        $returnval = $json->{returnval};
        return $returnval;
      }
    } elsif ($json->{msgtype} eq "command") {
      my $ret = 0;
      my %res;
      eval "\$ret=".$json->{command};
      if ($@) {
        Log3 $hash, 3, "Failed (".$json->{command}."): ".$@;
        %res = (
          awaitId => $json->{awaitId},
          error => 1,
          errorText => $@,
          result => $ret
        );
      } else {
        %res = (
          awaitId => $json->{awaitId},
          error => 0,
          result => $ret
        );
      }
      Log3 $hash, 3, "<<< WS: ".encode_json(\%res);
      DevIo_SimpleWrite($hash, encode_json(\%res), 0);
    }
  }
  return "continue";
}

1;

=pod
=item summary    Module for FHEMSync devices
=item summary_DE Modul zur Nutzung von FHEMSync Devices
=begin html

<a name="BindingsIo"></a>
<h3>BindingsIo</h3>
<ul>
  FHEMSync synced devices are using this module.<br><br>

  <a name="BindingsIo_Set"></a>
  <b>Set</b>
  <ul>
  Please see REMOTETYPE module commandref.
  </ul>

  <a name="BindingsIo_Get"></a>
  <b>Get</b>
  <ul>
  Please see REMOTETYPE module commandref.
  </ul>

  <a name="BindingsIo_Attr"></a>
  <b>Attr</b>
  <ul>
  Please set attributes on the remote device.
  </ul>
</ul><br>

=end html
=cut
